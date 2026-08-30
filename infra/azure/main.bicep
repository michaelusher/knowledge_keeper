// knowledge-keeper Azure infrastructure
// Deploys: Azure AI Search (with semantic ranker) + Azure OpenAI (embeddings + chat)
//
//   az group create -n kk-rg -l eastus2
//   az deployment group create -g kk-rg -f main.bicep -p namePrefix=kkprod
//
// After deployment, grant your identity (or the app's managed identity):
//   - "Search Index Data Contributor" on the search service
//   - "Cognitive Services OpenAI User" on the OpenAI account
// Then leave api keys empty in config.yaml to use DefaultAzureCredential.

@description('Prefix for resource names (lowercase alphanumeric)')
param namePrefix string

@description('Region for all resources')
param location string = resourceGroup().location

@description('Search SKU: basic is fine to start; standard for larger corpora')
@allowed(['basic', 'standard'])
param searchSku string = 'basic'

resource search 'Microsoft.Search/searchServices@2024-06-01-preview' = {
  name: '${namePrefix}-search'
  location: location
  sku: { name: searchSku }
  properties: {
    replicaCount: 1
    partitionCount: 1
    hostingMode: 'default'
    semanticSearch: 'standard'
    authOptions: { aadOrApiKey: { aadAuthFailureMode: 'http401WithBearerChallenge' } }
  }
}

resource openai 'Microsoft.CognitiveServices/accounts@2024-04-01-preview' = {
  name: '${namePrefix}-aoai'
  location: location
  kind: 'OpenAI'
  sku: { name: 'S0' }
  properties: {
    customSubDomainName: '${namePrefix}-aoai'
    publicNetworkAccess: 'Enabled'
  }
}

resource embeddingDeployment 'Microsoft.CognitiveServices/accounts/deployments@2024-04-01-preview' = {
  parent: openai
  name: 'text-embedding-3-large'
  sku: { name: 'Standard', capacity: 50 }
  properties: {
    model: { format: 'OpenAI', name: 'text-embedding-3-large', version: '1' }
  }
}

resource chatDeployment 'Microsoft.CognitiveServices/accounts/deployments@2024-04-01-preview' = {
  parent: openai
  name: 'gpt-4o'
  sku: { name: 'Standard', capacity: 30 }
  dependsOn: [embeddingDeployment]
  properties: {
    model: { format: 'OpenAI', name: 'gpt-4o', version: '2024-08-06' }
  }
}

output searchEndpoint string = 'https://${search.name}.search.windows.net'
output openaiEndpoint string = openai.properties.endpoint
