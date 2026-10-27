param location string
param environmentName string
param tags object
param environmentId string
param environmentDomain string
param registryId string
param registryLoginServer string
param vaultName string
param backendIdentityId string
param frontendIdentityId string
param backendImage string
param frontendImage string
param nextPublicClerkPublishableKey string
param storageBlobEndpoint string
param storageQueueEndpoint string
param cosmosEndpoint string

module events './container-app.bicep' = {
  name: 'events-api'
  params: {
    name: 'ca-events-whatshap-dev-04ce'
    location: location
    environmentId: environmentId
    environmentDomain: environmentDomain
    image: backendImage
    registryLoginServer: registryLoginServer
    identityId: backendIdentityId
    vaultName: vaultName
    port: 8000
    cpu: '0.25'
    memory: '0.5Gi'
    external: false
    healthPath: '/health'
    command: ['python', '-m', 'gunicorn', '--bind', '0.0.0.0:8000', '--timeout', '120', 'EventsApi.app:app']
    env: [
      { name: 'AZURE_CLIENT_ID', value: reference(backendIdentityId, '2024-11-30').clientId }
      { name: 'COSMOS_ACCOUNT_URI', value: cosmosEndpoint }
      { name: 'AZURE_STORAGE_BLOB_ENDPOINT', value: storageBlobEndpoint }
      { name: 'AZURE_STORAGE_QUEUE_ENDPOINT', value: storageQueueEndpoint }
      { name: 'DISCOVERY_API_URL', value: 'https://${discovery.outputs.fqdn}' }
    ]
    tags: tags
  }
}
module discovery './container-app.bicep' = {
  name: 'discovery-api'
  params: {
    name: 'ca-discovery-whatshap-dev-04ce'
    location: location
    environmentId: environmentId
    environmentDomain: environmentDomain
    image: backendImage
    registryLoginServer: registryLoginServer
    identityId: backendIdentityId
    vaultName: vaultName
    port: 5000
    cpu: '0.25'
    memory: '0.5Gi'
    external: false
    healthPath: '/health'
    command: ['python', '-m', 'gunicorn', '--bind', '0.0.0.0:5000', '--timeout', '120', 'EventDiscoveryApi.app:app']
    env: [
      { name: 'AZURE_CLIENT_ID', value: reference(backendIdentityId, '2024-11-30').clientId }
      { name: 'COSMOS_ACCOUNT_URI', value: cosmosEndpoint }
      { name: 'AZURE_STORAGE_BLOB_ENDPOINT', value: storageBlobEndpoint }
      { name: 'AZURE_STORAGE_QUEUE_ENDPOINT', value: storageQueueEndpoint }
    ]
    tags: tags
  }
}
module frontend './container-app.bicep' = {
  name: 'frontend'
  params: {
    name: 'ca-web-whatshap-dev-04ce'
    location: location
    environmentId: environmentId
    environmentDomain: environmentDomain
    image: frontendImage
    registryLoginServer: registryLoginServer
    identityId: frontendIdentityId
    vaultName: vaultName
    port: 3000
    cpu: '0.5'
    memory: '1Gi'
    external: true
    healthPath: '/api/health'
    command: []
    env: [
      { name: 'EVENTS_API_URL', value: 'https://${events.outputs.fqdn}' }
      { name: 'NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY', value: nextPublicClerkPublishableKey }
    ]
    secretNames: ['clerk-secret-key']
    secretName: 'clerk-secret-key'
    tags: tags
  }
}
module processor './container-app.bicep' = {
  name: 'processor'
  params: {
    name: 'ca-processor-whatshap-dev-04ce'
    location: location
    environmentId: environmentId
    environmentDomain: environmentDomain
    image: backendImage
    registryLoginServer: registryLoginServer
    identityId: backendIdentityId
    vaultName: vaultName
    port: 8080
    cpu: '0.25'
    memory: '0.5Gi'
    external: false
    healthPath: ''
    command: ['python', '-m', 'EventsProcessor.app']
    env: [
      { name: 'AZURE_CLIENT_ID', value: reference(backendIdentityId, '2024-11-30').clientId }
      { name: 'COSMOS_ACCOUNT_URI', value: cosmosEndpoint }
      { name: 'AZURE_STORAGE_BLOB_ENDPOINT', value: storageBlobEndpoint }
      { name: 'AZURE_STORAGE_QUEUE_ENDPOINT', value: storageQueueEndpoint }
    ]
    queueName: 'dundaaevents-discovered-events'
    tags: tags
  }
}
module scraper './container-app.bicep' = {
  name: 'scraper'
  params: {
    name: 'ca-scraper-whatshap-dev-04ce'
    location: location
    environmentId: environmentId
    environmentDomain: environmentDomain
    image: backendImage
    registryLoginServer: registryLoginServer
    identityId: backendIdentityId
    vaultName: vaultName
    port: 8080
    cpu: '0.25'
    memory: '0.5Gi'
    external: false
    healthPath: ''
    command: ['python', '-m', 'EventScrapper.app']
    env: [
      { name: 'AZURE_CLIENT_ID', value: reference(backendIdentityId, '2024-11-30').clientId }
      { name: 'COSMOS_ACCOUNT_URI', value: cosmosEndpoint }
      { name: 'AZURE_STORAGE_BLOB_ENDPOINT', value: storageBlobEndpoint }
      { name: 'AZURE_STORAGE_QUEUE_ENDPOINT', value: storageQueueEndpoint }
    ]
    queueName: 'x-delayed-dundaaevents-discovered-events'
    tags: tags
  }
}
resource scheduler 'Microsoft.App/jobs@2026-07-01' = {
  name: 'job-scrape-whatshap-dev-04ce'
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${backendIdentityId}': {}
    }
  }
  properties: {
    environmentId: environmentId
    configuration: {
      registries: [{ server: registryLoginServer, identity: backendIdentityId }]
      triggerType: 'Schedule'
      scheduleTriggerConfig: {
        cronExpression: '0 */6 * * *'
        parallelism: 1
        replicaCompletionCount: 1
      }
      replicaRetryLimit: 1
      replicaTimeout: 1800
    }
    template: {
      containers: [
        {
          name: 'scheduler'
          image: backendImage
          command: ['python', '-m', 'EventDiscoveryApi.schedule']
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
          env: [
            { name: 'AZURE_CLIENT_ID', value: reference(backendIdentityId, '2024-11-30').clientId }
            { name: 'COSMOS_ACCOUNT_URI', value: cosmosEndpoint }
            { name: 'AZURE_STORAGE_BLOB_ENDPOINT', value: storageBlobEndpoint }
            { name: 'AZURE_STORAGE_QUEUE_ENDPOINT', value: storageQueueEndpoint }
          ]
        }
      ]
    }
  }
}

output frontendUrl string = 'https://${frontend.outputs.fqdn}'
output eventsApiUrl string = 'https://${events.outputs.fqdn}'
output discoveryApiUrl string = 'https://${discovery.outputs.fqdn}'
output schedulerJobName string = scheduler.name
