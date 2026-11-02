variable "project_name" {
  description = "Project name used as prefix for resources"
  type        = string
  default     = "software-factory"
}

variable "environment" {
  description = "Environment name (dev, staging, prod)"
  type        = string
  default     = "dev"
}

variable "region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "vpc_cidr" {
  description = "CIDR block for VPC"
  type        = string
  default     = "10.0.0.0/16"
}

variable "eks_cluster_version" {
  description = "Kubernetes version for EKS cluster"
  type        = string
  default     = "1.28"
}

variable "system_node_instance_types" {
  description = "Instance types for system node group (always-on workloads)"
  type        = list(string)
  default     = ["t3.medium"]
}

variable "system_node_desired_size" {
  description = "Desired number of system nodes"
  type        = number
  default     = 2
}

variable "system_node_min_size" {
  description = "Minimum number of system nodes"
  type        = number
  default     = 2
}

variable "system_node_max_size" {
  description = "Maximum number of system nodes"
  type        = number
  default     = 4
}

variable "sandbox_node_instance_types" {
  description = "Instance types for sandbox node group (ephemeral workloads)"
  type        = list(string)
  default     = ["t3.medium", "t3a.medium"]
}

variable "sandbox_node_desired_size" {
  description = "Desired number of sandbox nodes"
  type        = number
  default     = 1
}

variable "sandbox_node_min_size" {
  description = "Minimum number of sandbox nodes"
  type        = number
  default     = 0
}

variable "sandbox_node_max_size" {
  description = "Maximum number of sandbox nodes"
  type        = number
  default     = 10
}

variable "use_spot_instances" {
  description = "Use spot instances for sandbox node group"
  type        = bool
  default     = true
}

variable "enable_gvisor" {
  description = "Configure gVisor runtime class (requires compatible AMI)"
  type        = bool
  default     = false
}

variable "postgres_instance_class" {
  description = "RDS instance class"
  type        = string
  default     = "db.t3.micro"
}

variable "postgres_allocated_storage" {
  description = "RDS allocated storage in GB"
  type        = number
  default     = 20
}

variable "postgres_backup_retention_days" {
  description = "Number of days to retain RDS backups"
  type        = number
  default     = 7
}

variable "tags" {
  description = "Additional tags for all resources"
  type        = map(string)
  default     = {}
}
