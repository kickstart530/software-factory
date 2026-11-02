# VPC for the software factory
# Creates isolated network with public and private subnets

# Placeholder: would use terraform-aws-modules/vpc/aws or custom module
# 
# module "vpc" {
#   source  = "terraform-aws-modules/vpc/aws"
#   version = "~> 5.0"
#   
#   name = "${var.project_name}-${var.environment}"
#   cidr = var.vpc_cidr
#   
#   azs             = ["${var.region}a", "${var.region}b", "${var.region}c"]
#   private_subnets = [cidrsubnet(var.vpc_cidr, 4, 0), cidrsubnet(var.vpc_cidr, 4, 1), cidrsubnet(var.vpc_cidr, 4, 2)]
#   public_subnets  = [cidrsubnet(var.vpc_cidr, 4, 8), cidrsubnet(var.vpc_cidr, 4, 9), cidrsubnet(var.vpc_cidr, 4, 10)]
#   
#   enable_nat_gateway   = true
#   single_nat_gateway   = var.environment == "dev"
#   enable_dns_hostnames = true
#   enable_dns_support   = true
#   
#   tags = {
#     "kubernetes.io/cluster/${var.project_name}-${var.environment}" = "shared"
#   }
#   
#   public_subnet_tags = {
#     "kubernetes.io/role/elb" = "1"
#   }
#   
#   private_subnet_tags = {
#     "kubernetes.io/role/internal-elb" = "1"
#   }
# }
