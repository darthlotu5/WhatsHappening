param location string
param environmentName string
param tags object

resource logs 'Microsoft.OperationalInsights/workspaces@2026-03-01' = {
  name: 'log-whatshap-dev-04ce'
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
    workspaceCapping: {
      dailyQuotaGb: json('0.1')
    }
  }
}

resource env 'Microsoft.App/managedEnvironments@2026-07-01' = {
  name: 'cae-whatshap-dev-04ce'
  location: location
  tags: tags
  properties: {
    workloadProfiles: [
      { name: 'Consumption', workloadProfileType: 'Consumption' }
    ]
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
    }
  }
}

resource registry 'Microsoft.ContainerRegistry/registries@2025-11-01' = {
  name: 'crwhatshapdev04ce'
  location: location
  tags: tags
  sku: { name: 'Basic' }
  properties: { adminUserEnabled: false }
}

resource backendIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' = {
  name: 'id-data-whatshap-dev-04ce'
  location: location
  tags: tags
}

resource frontendIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' = {
  name: 'id-web-whatshap-dev-04ce'
  location: location
  tags: tags
}

resource storage 'Microsoft.Storage/storageAccounts@2026-09-01' = {
  name: 'stwhatshapdev04ce'
  location: location
  tags: tags
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    supportsHttpsTrafficOnly: true
    allowBlobPublicAccess: false
    minimumTlsVersion: 'TLS1_2'
  }
}
resource blobs 'Microsoft.Storage/storageAccounts/blobServices@2026-09-01' = {
  parent: storage
  name: 'default'
}
resource queues 'Microsoft.Storage/storageAccounts/queueServices@2026-09-01' = {
  parent: storage
  name: 'default'
}
resource scrapeQueue 'Microsoft.Storage/storageAccounts/queueServices/queues@2026-09-01' = {
  parent: queues
  name: 'x-delayed-dundaaevents-discovered-events'
}
resource processQueue 'Microsoft.Storage/storageAccounts/queueServices/queues@2026-09-01' = {
  parent: queues
  name: 'dundaaevents-discovered-events'
}

resource cosmos 'Microsoft.DocumentDB/databaseAccounts@2026-03-15' = {
  name: 'cosmos-whatshap-dev-04ce'
  location: location
  tags: tags
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    capacity: { totalThroughputLimit: 1000 }
    capabilities: [{ name: 'EnableServerless' }]
    disableLocalAuth: true
    consistencyPolicy: { defaultConsistencyLevel: 'Session' }
    locations: [{ locationName: location, failoverPriority: 0, isZoneRedundant: false }]
  }
}
resource db 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2026-03-15' = {
  parent: cosmos
  name: 'dundaa'
  properties: { resource: { id: 'dundaa' } }
}
resource container 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2026-03-15' = {
  parent: db
  name: 'site_events'
  properties: {
    resource: {
      id: 'site_events'
      partitionKey: { paths: ['/site_name'], kind: 'Hash' }
    }
  }
}

resource vault 'Microsoft.KeyVault/vaults@2026-05-15' = {
  name: 'kv-whatshap-dev-04ce'
  location: location
  tags: tags
  properties: {
    sku: { family: 'A', name: 'standard' }
    tenantId: subscription().tenantId
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
    networkAcls: { defaultAction: 'Allow', bypass: 'AzureServices' }
  }
}
resource insights 'Microsoft.Insights/components@2020-02-02' = {
  name: 'appi-whatshap-dev-04ce'
  location: location
  tags: tags
  kind: 'web'
  properties: { Application_Type: 'web', WorkspaceResourceId: logs.id }
}

output environmentId string = env.id
output environmentDomain string = env.properties.defaultDomain
output registryId string = registry.id
output registryName string = registry.name
output registryLoginServer string = registry.properties.loginServer
output vaultId string = vault.id
output vaultName string = vault.name
output storageId string = storage.id
output storageName string = storage.name
output storageBlobEndpoint string = storage.properties.primaryEndpoints.blob
output storageQueueEndpoint string = storage.properties.primaryEndpoints.queue
output cosmosId string = cosmos.id
output cosmosName string = cosmos.name
output cosmosEndpoint string = cosmos.properties.documentEndpoint
output backendIdentityId string = backendIdentity.id
output backendIdentityPrincipalId string = backendIdentity.properties.principalId
output backendIdentityClientId string = backendIdentity.properties.clientId
output frontendIdentityId string = frontendIdentity.id
output frontendIdentityPrincipalId string = frontendIdentity.properties.principalId
output frontendIdentityClientId string = frontendIdentity.properties.clientId
