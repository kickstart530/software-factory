# AWS Infrastructure for Software Factory

Terraform configuration for deploying the software factory on AWS.

## Prerequisites

- AWS CLI configured with appropriate credentials
- Terraform >= 1.5
- kubectl

## Resources Created

- **EKS Cluster**: Kubernetes 1.28+ with managed node groups
- **RDS PostgreSQL**: Database for work orders, tasks, and audit logs
- **S3 Buckets**: Artifacts, checkpoints, and transcripts
- **VPC**: Isolated network with public and private subnets
- **IAM Roles**: IRSA (IAM Roles for Service Accounts) for workload identity
- **Security Groups**: Controlled ingress/egress for all components

## Quick Start

```bash
# 1. Configure backend
cp backend.tf.example backend.tf
# Edit backend.tf with your S3 bucket and DynamoDB table

# 2. Configure variables
cp environments/dev.tfvars.example environments/dev.tfvars
# Edit environments/dev.tfvars with your settings

# 3. Initialize and apply
terraform init
terraform plan -var-file=environments/dev.tfvars
terraform apply -var-file=environments/dev.tfvars

# 4. Configure kubectl
aws eks update-kubeconfig --region <region> --name <cluster-name>

# 5. Deploy Kubernetes manifests
kubectl apply -f ../../k8s/base/
```

## Variables

See `variables.tf` for full documentation. Key variables:

- `project_name`: Prefix for all resources
- `environment`: Environment name (dev, staging, prod)
- `region`: AWS region
- `eks_cluster_version`: Kubernetes version
- `enable_gvisor`: Whether to configure gVisor runtime (requires compatible AMI)
- `sandbox_node_instance_types`: Instance types for sandbox node group
- `use_spot_instances`: Use spot instances for sandbox nodes (recommended)

## Outputs

After apply, Terraform outputs:

- `cluster_endpoint`: EKS cluster endpoint
- `cluster_name`: Name for kubectl configuration
- `postgres_endpoint`: RDS PostgreSQL endpoint
- `s3_buckets`: Names of created S3 buckets
- `otel_collector_role_arn`: IAM role ARN for OTel collector

## Cost Estimate

Development environment (us-east-1):
- EKS control plane: ~$73/month
- 2× t3.medium nodes (system): ~$60/month
- Sandbox nodes (autoscaled): variable, ~$0.03/hour per t3.medium spot
- RDS db.t3.micro: ~$15/month
- S3: ~$5/month (first 10GB)
- **Total**: ~$153/month + sandbox compute

## Cleanup

```bash
terraform destroy -var-file=environments/dev.tfvars
```

Note: Persistent data (RDS snapshots, S3 buckets) may have retention policies that require manual deletion.
