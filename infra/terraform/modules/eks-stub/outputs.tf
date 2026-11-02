# Stub module to allow main.tf to reference module.eks outputs
# In a complete implementation, this would be replaced by the official EKS module

output "cluster_endpoint" {
  value = "https://example.eks.amazonaws.com"
}

output "cluster_name" {
  value = "software-factory-dev"
}

output "cluster_certificate_authority_data" {
  value = base64encode("stub-ca-data")
}

output "cluster_security_group_id" {
  value = "sg-stub123456"
}
