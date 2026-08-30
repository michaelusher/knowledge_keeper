# knowledge-keeper AWS infrastructure
# Deploys: OpenSearch Serverless vector collection + policies, and an IAM
# policy granting Bedrock model invocation (Titan embeddings + Claude).
#
#   terraform init && terraform apply -var="name_prefix=kkprod" -var="principal_arn=arn:aws:iam::<acct>:role/<your-role>"
#
# Note: Bedrock model access must also be enabled once per account/region in
# the Bedrock console ("Model access") for Titan Embeddings and Claude.

terraform {
  required_providers {
    aws = { source = "hashicorp/aws", version = ">= 5.40" }
  }
}

variable "name_prefix"   { type = string }
variable "region"        { type = string, default = "us-east-1" }
variable "principal_arn" { type = string, description = "IAM role/user ARN that runs knowledge-keeper" }

provider "aws" { region = var.region }

resource "aws_opensearchserverless_security_policy" "encryption" {
  name = "${var.name_prefix}-enc"
  type = "encryption"
  policy = jsonencode({
    Rules = [{ ResourceType = "collection", Resource = ["collection/${var.name_prefix}-kb"] }]
    AWSOwnedKey = true
  })
}

resource "aws_opensearchserverless_security_policy" "network" {
  name = "${var.name_prefix}-net"
  type = "network"
  policy = jsonencode([{
    Rules = [
      { ResourceType = "collection", Resource = ["collection/${var.name_prefix}-kb"] },
      { ResourceType = "dashboard",  Resource = ["collection/${var.name_prefix}-kb"] }
    ]
    AllowFromPublic = true # tighten to VPC endpoints for production
  }])
}

resource "aws_opensearchserverless_collection" "kb" {
  name       = "${var.name_prefix}-kb"
  type       = "VECTORSEARCH"
  depends_on = [aws_opensearchserverless_security_policy.encryption]
}

resource "aws_opensearchserverless_access_policy" "data" {
  name = "${var.name_prefix}-data"
  type = "data"
  policy = jsonencode([{
    Rules = [
      {
        ResourceType = "collection"
        Resource     = ["collection/${var.name_prefix}-kb"]
        Permission   = ["aoss:CreateCollectionItems", "aoss:UpdateCollectionItems", "aoss:DescribeCollectionItems"]
      },
      {
        ResourceType = "index"
        Resource     = ["index/${var.name_prefix}-kb/*"]
        Permission   = ["aoss:CreateIndex", "aoss:UpdateIndex", "aoss:DescribeIndex", "aoss:ReadDocument", "aoss:WriteDocument"]
      }
    ]
    Principal = [var.principal_arn]
  }])
}

resource "aws_iam_policy" "bedrock_invoke" {
  name = "${var.name_prefix}-bedrock-invoke"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["bedrock:InvokeModel", "bedrock:Converse"]
      Resource = "arn:aws:bedrock:${var.region}::foundation-model/*"
    }]
  })
}

output "opensearch_endpoint" { value = aws_opensearchserverless_collection.kb.collection_endpoint }
output "bedrock_policy_arn"  { value = aws_iam_policy.bedrock_invoke.arn }
