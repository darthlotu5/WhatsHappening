param name string
param location string
param environmentId string
param environmentDomain string
@minLength(1)
param image string
param registryLoginServer string
param identityId string
param vaultName string
param port int
param cpu string
param memory string
param external bool
param healthPath string
param command array
param env array
param tags object
param queueName string = ''
param secretNames array = []
param secretName string = ''

var appEnv = concat(env, [{ name: 'PORT', value: string(port) }])
var secretRefs = [
  for secretName in secretNames: {
    name: secretName
    keyVaultUrl: 'https://${vaultName}.vault.azure.net/secrets/${secretName}'
    identity: identityId
  }
]

resource app 'Microsoft.App/containerApps@2026-07-01' = {
  name: name
  location: location
  tags: tags
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${identityId}': {} } }
  properties: {
    managedEnvironmentId: environmentId
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: empty(queueName)
        ? {
            external: external
            targetPort: port
            transport: 'auto'
            allowInsecure: false
          }
        : null
      registries: [{ server: registryLoginServer, identity: identityId }]
      secrets: secretRefs
    }
    template: {
      containers: [
        {
          name: name
          image: image
          command: command
          resources: { cpu: json(cpu), memory: memory }
          env: concat(appEnv, empty(secretName) ? [] : [{ name: 'CLERK_SECRET_KEY', secretRef: secretName }])
          probes: empty(healthPath)
            ? []
            : [
                {
                  type: 'Liveness'
                  httpGet: { path: healthPath, port: port }
                  initialDelaySeconds: 10
                  periodSeconds: 30
                }
                {
                  type: 'Readiness'
                  httpGet: { path: healthPath, port: port }
                  initialDelaySeconds: 5
                  periodSeconds: 10
                }
              ]
        }
      ]
      scale: {
        minReplicas: 0
        maxReplicas: 1
        rules: empty(queueName)
          ? [{ name: 'http-scale', http: { metadata: { concurrentRequests: '10' } } }]
          : [
              {
                name: 'queue-scale'
                custom: {
                  type: 'azure-queue'
                  metadata: { accountName: 'stwhatshapdev04ce', queueName: queueName, queueLength: '1' }
                  identity: identityId
                }
              }
            ]
      }
    }
  }
}
output fqdn string = empty(queueName) ? app.properties.configuration.ingress.fqdn : ''
