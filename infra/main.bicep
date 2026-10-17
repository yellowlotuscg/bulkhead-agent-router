targetScope = 'resourceGroup'

@description('Azure region for all resources.')
param location string = resourceGroup().location
@description('Globally unique resource name prefix.')
param namePrefix string = 'bulkhead-router-dev'
@description('GitHub organization or user for OIDC federation.')
param githubOrganization string = 'example-org'
@description('GitHub repository name for OIDC federation.')
param githubRepository string = 'bulkhead-agent-router'
@description('GitHub branch allowed to deploy.')
param githubBranch string = 'main'

var serviceBusName = '${namePrefix}-sb'
var storageName = take('st${uniqueString(resourceGroup().id)}', 24)
var functionName = '${namePrefix}-fn'
var appInsightsName = '${namePrefix}-ai'
var logName = '${namePrefix}-logs'
var cosmosName = '${namePrefix}-audit'
var queueName = 'tasks'

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageName
  location: location
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    allowBlobPublicAccess: false
    minimumTlsVersion: 'TLS1_2'
  }
}

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2022-10-01' = {
  name: logName
  location: location
  properties: { retentionInDays: 30 }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: appInsightsName
  location: location
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalytics.id
  }
}

resource serviceBus 'Microsoft.ServiceBus/namespaces@2022-10-01-preview' = {
  name: serviceBusName
  location: location
  sku: { name: 'Standard', tier: 'Standard' }
  properties: {
    publicNetworkAccess: 'Disabled'
    disableLocalAuth: true
  }
}

resource taskQueue 'Microsoft.ServiceBus/namespaces/queues@2022-10-01-preview' = {
  parent: serviceBus
  name: queueName
  properties: {
    requiresSession: true
    deadLetteringOnMessageExpiration: true
    maxDeliveryCount: 10
    lockDuration: 'PT1M'
  }
}

resource cosmos 'Microsoft.DocumentDB/databaseAccounts@2024-05-15' = {
  name: cosmosName
  location: location
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    enableFreeTier: true
    capabilities: [{ name: 'EnableServerless' }]
    consistencyPolicy: { defaultConsistencyLevel: 'Session' }
    locations: [{ locationName: location, failoverPriority: 0 }]
  }
}

resource cosmosDatabase 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-05-15' = {
  parent: cosmos
  name: 'auditdb'
  properties: { resource: { id: 'auditdb' } }
}

resource cosmosContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-05-15' = {
  parent: cosmosDatabase
  name: 'events'
  properties: {
    resource: {
      id: 'events'
      partitionKey: { paths: ['/tenant'], kind: 'Hash' }
    }
  }
}

resource plan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: '${namePrefix}-plan'
  location: location
  kind: 'linux'
  sku: { name: 'Y1', tier: 'Dynamic' }
  properties: { reserved: true }
}

resource functionApp 'Microsoft.Web/sites@2023-12-01' = {
  name: functionName
  location: location
  kind: 'functionapp,linux'
  identity: { type: 'SystemAssigned' }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'Python|3.12'
      minTlsVersion: '1.2'
      appSettings: [
        { name: 'FUNCTIONS_WORKER_RUNTIME', value: 'python' }
        { name: 'FUNCTIONS_EXTENSION_VERSION', value: '~4' }
        { name: 'AzureWebJobsStorage', value: 'DefaultEndpointsProtocol=https;AccountName=${storage.name};EndpointSuffix=${environment().suffixes.storage}' }
        { name: 'APPINSIGHTS_INSTRUMENTATIONKEY', value: appInsights.properties.InstrumentationKey }
        { name: 'SERVICE_BUS_QUEUE', value: queueName }
        { name: 'COSMOS_ENDPOINT', value: cosmos.properties.documentEndpoint }
      ]
    }
  }
}

resource deployIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${namePrefix}-deploy-id'
  location: location
}

resource federatedCredential 'Microsoft.ManagedIdentity/userAssignedIdentities/federatedIdentityCredentials@2023-01-31' = {
  parent: deployIdentity
  name: 'github-actions'
  properties: {
    audiences: ['api://AzureADTokenExchange']
    issuer: 'https://token.actions.githubusercontent.com'
    subject: 'repo:${githubOrganization}/${githubRepository}:ref:refs/heads/${githubBranch}'
  }
}

var serviceBusSenderRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '2b629674-e913-4c01-ae53-ef4638d8f975')
var serviceBusReceiverRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4a9ae827-6dc8-4573-8ac7-823c75c1a40f')
var cosmosContributorRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '5bd9cd88-fe45-4216-938b-f97437e15450')

resource functionServiceBusReceiver 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(serviceBus.id, functionName, serviceBusReceiverRole)
  scope: serviceBus
  properties: { roleDefinitionId: serviceBusReceiverRole, principalId: functionApp.identity.principalId, principalType: 'ServicePrincipal' }
}

resource functionServiceBusSender 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(serviceBus.id, functionName, serviceBusSenderRole)
  scope: serviceBus
  properties: { roleDefinitionId: serviceBusSenderRole, principalId: functionApp.identity.principalId, principalType: 'ServicePrincipal' }
}

resource functionCosmosContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(cosmos.id, functionName, cosmosContributorRole)
  scope: cosmos
  properties: { roleDefinitionId: cosmosContributorRole, principalId: functionApp.identity.principalId, principalType: 'ServicePrincipal' }
}

resource deployServiceBusSender 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(serviceBus.id, '${namePrefix}-deploy-id', serviceBusSenderRole)
  scope: serviceBus
  properties: { roleDefinitionId: serviceBusSenderRole, principalId: deployIdentity.properties.principalId, principalType: 'ServicePrincipal' }
}

output functionAppName string = functionApp.name
output serviceBusNamespace string = serviceBus.name
output cosmosEndpoint string = cosmos.properties.documentEndpoint
output deployClientId string = deployIdentity.properties.clientId
