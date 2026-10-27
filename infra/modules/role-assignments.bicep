param registryId string
param vaultId string
param storageId string
param cosmosId string
param backendIdentityPrincipalId string
param frontendIdentityPrincipalId string
param deployerObjectId string
param tags object
resource registry 'Microsoft.ContainerRegistry/registries@2025-11-01' existing = { name: 'crwhatshapdev04ce' }
resource vault 'Microsoft.KeyVault/vaults@2026-05-15' existing = { name: 'kv-whatshap-dev-04ce' }
resource storage 'Microsoft.Storage/storageAccounts@2026-09-01' existing = { name: 'stwhatshapdev04ce' }
resource cosmos 'Microsoft.DocumentDB/databaseAccounts@2026-03-15' existing = { name: 'cosmos-whatshap-dev-04ce' }

var acrPull = '7f951dda-4ed3-4680-a7ca-43fe172d538d'
var kvUser = '4633458b-17de-408a-b874-0445c86b69e6'
var kvOfficer = 'b86a8fe4-44ce-4948-aee5-eccb2c155cd7'
var blobContributor = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
var queueContributor = '974c5e8b-45b9-4653-ba55-5f855dd0fb88'

resource backendAcr 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registryId, backendIdentityPrincipalId, acrPull)
  scope: registry
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPull)
    principalId: backendIdentityPrincipalId
    principalType: 'ServicePrincipal'
  }
}
resource frontendAcr 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registryId, frontendIdentityPrincipalId, acrPull)
  scope: registry
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPull)
    principalId: frontendIdentityPrincipalId
    principalType: 'ServicePrincipal'
  }
}
resource backendBlob 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storageId, backendIdentityPrincipalId, blobContributor)
  scope: storage
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', blobContributor)
    principalId: backendIdentityPrincipalId
    principalType: 'ServicePrincipal'
  }
}
resource backendQueue 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storageId, backendIdentityPrincipalId, queueContributor)
  scope: storage
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', queueContributor)
    principalId: backendIdentityPrincipalId
    principalType: 'ServicePrincipal'
  }
}
resource frontendKv 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(vaultId, frontendIdentityPrincipalId, kvUser)
  scope: vault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', kvUser)
    principalId: frontendIdentityPrincipalId
    principalType: 'ServicePrincipal'
  }
}
resource deployerKv 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(vaultId, deployerObjectId, kvOfficer)
  scope: vault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', kvOfficer)
    principalId: deployerObjectId
    principalType: 'User'
  }
}
resource cosmosData 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2026-03-15' = {
  name: guid(cosmosId, backendIdentityPrincipalId, 'cosmos-data-contributor')
  parent: cosmos
  properties: {
    roleDefinitionId: '${cosmosId}/sqlRoleDefinitions/00000000-0000-0000-0000-000000000002'
    principalId: backendIdentityPrincipalId
    scope: cosmosId
  }
}
