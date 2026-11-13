# Llama Stack distribution on ECS Fargate (private), for the meta_llama_aws profile. It is
# applied together with clouds/aws/terraform (state tables, telemetry, ECR). The task role
# may invoke the Llama models on Bedrock; pgvector credentials come from Secrets Manager.
terraform {
  required_version = ">= 1.7"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = var.region
}

locals {
  name = "e2a-${var.tenant_id}-llama-stack"
}

resource "aws_ecs_cluster" "this" {
  name = local.name
  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

resource "aws_cloudwatch_log_group" "this" {
  name              = "/ecs/${local.name}"
  retention_in_days = 30
}

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "task" {
  name               = "${local.name}-task"
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

resource "aws_iam_role" "execution" {
  name               = "${local.name}-exec"
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

data "aws_iam_policy_document" "task" {
  statement {
    sid       = "LlamaOnBedrock"
    actions   = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
    resources = [for m in var.bedrock_model_ids : "arn:aws:bedrock:*::foundation-model/${m}"]
  }
}

resource "aws_iam_role_policy" "task" {
  name   = "llama-stack-inference"
  role   = aws_iam_role.task.id
  policy = data.aws_iam_policy_document.task.json
}

resource "aws_ecs_task_definition" "this" {
  family                   = local.name
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 2048
  memory                   = 4096
  task_role_arn            = aws_iam_role.task.arn
  execution_role_arn       = aws_iam_role.execution.arn
  container_definitions = jsonencode([{
    name         = "llama-stack"
    image        = var.image
    essential    = true
    command      = ["--config", "/config/run.yaml"]
    portMappings = [{ containerPort = 8321, protocol = "tcp" }]
    environment = [
      { name = "AWS_REGION", value = var.region },
      { name = "PGVECTOR_HOST", value = var.pgvector_host },
      { name = "LLAMA_GUARD_URL", value = var.llama_guard_url },
    ]
    secrets = [{ name = "PGVECTOR_PASSWORD", valueFrom = var.pgvector_password_secret_arn }]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.this.name
        awslogs-region        = var.region
        awslogs-stream-prefix = "llama-stack"
      }
    }
  }])
}

resource "aws_security_group" "this" {
  name   = local.name
  vpc_id = var.vpc_id
  ingress {
    from_port       = 8321
    to_port         = 8321
    protocol        = "tcp"
    security_groups = var.client_security_group_ids # only the E2A services may call it
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_ecs_service" "this" {
  name            = local.name
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.this.arn
  desired_count   = var.desired_count
  launch_type     = "FARGATE"
  network_configuration {
    subnets          = var.private_subnet_ids
    security_groups  = [aws_security_group.this.id]
    assign_public_ip = false
  }
}
