# RDS PostgreSQL for factory metadata
# Stores work orders, tasks, audit logs, budgets

resource "aws_db_subnet_group" "factory" {
  name       = "${var.project_name}-${var.environment}"
  subnet_ids = [] # Would be: module.vpc.private_subnets

  tags = {
    Name = "${var.project_name}-${var.environment}-db-subnet-group"
  }
}

resource "aws_security_group" "rds" {
  name        = "${var.project_name}-${var.environment}-rds"
  description = "Security group for RDS PostgreSQL"
  vpc_id      = "" # Would be: module.vpc.vpc_id

  ingress {
    description = "PostgreSQL from EKS"
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    security_groups = [] # Would be: [module.eks.cluster_security_group_id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "${var.project_name}-${var.environment}-rds-sg"
  }
}

resource "random_password" "postgres" {
  length  = 32
  special = true
}

resource "aws_db_instance" "factory" {
  identifier     = "${var.project_name}-${var.environment}"
  engine         = "postgres"
  engine_version = "16"
  instance_class = var.postgres_instance_class

  allocated_storage     = var.postgres_allocated_storage
  max_allocated_storage = var.postgres_allocated_storage * 2
  storage_type          = "gp3"
  storage_encrypted     = true

  db_name  = "factory"
  username = "factory_admin"
  password = random_password.postgres.result

  db_subnet_group_name   = aws_db_subnet_group.factory.name
  vpc_security_group_ids = [aws_security_group.rds.id]

  backup_retention_period = var.postgres_backup_retention_days
  backup_window           = "03:00-04:00"
  maintenance_window      = "mon:04:00-mon:05:00"

  skip_final_snapshot       = var.environment == "dev"
  final_snapshot_identifier = "${var.project_name}-${var.environment}-final-snapshot"

  enabled_cloudwatch_logs_exports = ["postgresql"]

  tags = {
    Name = "${var.project_name}-${var.environment}-postgres"
  }
}

# Store password in Secrets Manager
resource "aws_secretsmanager_secret" "postgres" {
  name        = "${var.project_name}/${var.environment}/postgres"
  description = "PostgreSQL credentials for software factory"

  tags = {
    Name = "${var.project_name}-${var.environment}-postgres-secret"
  }
}

resource "aws_secretsmanager_secret_version" "postgres" {
  secret_id = aws_secretsmanager_secret.postgres.id
  secret_string = jsonencode({
    username = aws_db_instance.factory.username
    password = random_password.postgres.result
    endpoint = aws_db_instance.factory.endpoint
    database = aws_db_instance.factory.db_name
    port     = aws_db_instance.factory.port
  })
}
