targetScope = 'subscription'

param environmentName string
param location string
param sessionId string
param deployedBy string
param createdAt string
param deployerObjectId string
param deployApps bool = false
@description('Optional shared backend image alias; backendImage may override it.')
param containerImage string = ''
param backendImage string = containerImage
param frontendImage string = ''
param nextPublicClerkPublishableKey string = ''

var tags = {
  'app-onboard-skill': 'true'
  'app-onboard-session-id': sessionId
  'created-at': createdAt
  environment: environmentName
  'deployed-by': deployedBy
}
var rgName = 'rg-whatshap-dev-04ce'

resource rg 'Microsoft.Resources/resourceGroups@2025-04-01' = {
  name: rgName
  location: location
  tags: tags
}

module foundation './modules/foundation.bicep' = {
  name: 'foundation'
  scope: rg
  params: {
    location: location
    environmentName: environmentName
    tags: tags
  }
}

module roles './modules/role-assignments.bicep' = {
  name: 'roles'
  scope: rg
  params: {
    registryId: foundation.outputs.registryId
    vaultId: foundation.outputs.vaultId
    storageId: foundation.outputs.storageId
    cosmosId: foundation.outputs.cosmosId
    backendIdentityPrincipalId: foundation.outputs.backendIdentityPrincipalId
    frontendIdentityPrincipalId: foundation.outputs.frontendIdentityPrincipalId
    deployerObjectId: deployerObjectId
    tags: tags
  }
}

module apps './modules/apps.bicep' = if (deployApps) {
  name: 'apps'
  scope: rg
  dependsOn: [roles]
  params: {
    location: location
    environmentName: environmentName
    tags: tags
    environmentId: foundation.outputs.environmentId
    environmentDomain: foundation.outputs.environmentDomain
    registryId: foundation.outputs.registryId
    registryLoginServer: foundation.outputs.registryLoginServer
    vaultName: foundation.outputs.vaultName
    backendIdentityId: foundation.outputs.backendIdentityId
    frontendIdentityId: foundation.outputs.frontendIdentityId
    backendImage: backendImage
    frontendImage: frontendImage
    nextPublicClerkPublishableKey: nextPublicClerkPublishableKey
    storageBlobEndpoint: foundation.outputs.storageBlobEndpoint
    storageQueueEndpoint: foundation.outputs.storageQueueEndpoint
    cosmosEndpoint: foundation.outputs.cosmosEndpoint
  }
}

output resourceGroupName string = rg.name
output environmentId string = foundation.outputs.environmentId
output registryName string = foundation.outputs.registryName
output registryLoginServer string = foundation.outputs.registryLoginServer
output storageName string = foundation.outputs.storageName
output cosmosName string = foundation.outputs.cosmosName
output vaultName string = foundation.outputs.vaultName
output backendIdentityId string = foundation.outputs.backendIdentityId
output frontendIdentityId string = foundation.outputs.frontendIdentityId
output frontendUrl string = deployApps ? apps.outputs.frontendUrl : ''
output eventsApiUrl string = deployApps ? apps.outputs.eventsApiUrl : ''
output discoveryApiUrl string = deployApps ? apps.outputs.discoveryApiUrl : ''
output schedulerJobName string = deployApps ? apps.outputs.schedulerJobName : ''
