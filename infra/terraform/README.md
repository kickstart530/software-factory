# Cloud Infrastructure (Terraform)

Terraform modules for deploying the software factory to AWS, GCP, or Azure.

## Structure

```
terraform/
├── aws/          # AWS-specific resources (EKS, RDS, S3)
├── gcp/          # GCP-specific resources (GKE, Cloud SQL, GCS)
├── azure/        # Azure-specific resources (AKS, PostgreSQL, Blob Storage)
└── modules/      # Shared modules (networking, observability)
```

## Quick Start (AWS Example)

```bash
cd infra/terraform/aws

# Initialize
terraform init

# Plan
terraform plan -var-file=environments/dev.tfvars

# Apply
terraform apply -var-file=environments/dev.tfvars
```

## What Gets Deployed

### Compute
- Managed Kubernetes cluster (EKS/GKE/AKS)
- Node groups: system (always-on) and sandboxes (autoscaled, spot/preemptible)
- Container runtime with gVisor support where available

### Data Layer
- Managed PostgreSQL (RDS/Cloud SQL/Azure Database)
- Object storage (S3/GCS/Azure Blob) for artifacts, checkpoints, transcripts
- Backup and retention policies

### Networking
- VPC with public and private subnets
- NAT gateway for egress
- Network policies for sandbox isolation

### Observability
- OpenTelemetry Collector (deployed via Helm after cluster creation)
- Integration with cloud-native observability (CloudWatch/Cloud Logging/Azure Monitor)
- Optional: Managed Grafana/Tempo

### Security
- OIDC provider for workload identity
- KMS for encryption at rest
- Secrets manager integration
- RBAC and pod security standards

## Cost Optimization

- Sandbox nodes use spot/preemptible instances (safe due to checkpointing)
- Database uses right-sized instances (can scale up later)
- Object storage uses lifecycle policies for old transcripts
- Autoscaling based on pending sandbox jobs

## State Management

Terraform state should be stored in a remote backend:
- AWS: S3 + DynamoDB for locking
- GCP: GCS
- Azure: Azure Storage

Example backend configuration is in each cloud directory's `backend.tf.example`.

## Next Steps

1. Copy `backend.tf.example` to `backend.tf` and configure your state backend
2. Copy `environments/dev.tfvars.example` to `environments/dev.tfvars` and set your values
3. Run `terraform init` and `terraform apply`
4. Deploy Kubernetes manifests from `infra/k8s/` using kubectl or Helm
