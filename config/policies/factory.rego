# Factory Policy Rules (OPA/Rego)
#
# This policy decides what operations each agent may perform, based on:
# - Agent role
# - Tool being called
# - Risk tier of the Work Order
# - Whether untrusted content is in context (tainted)
#
# Policy result format (returned as data.factory.allow):
# {
#   "allow": bool,                    # whether to permit the operation
#   "reason": string,                 # explanation if denied
#   "rule": string,                   # rule identifier
#   "requires_approval": bool,        # whether human approval is needed
#   "approval_reason": string         # why approval is needed
# }

package factory

import future.keywords.if
import future.keywords.in

# Default: allow
default allow := {
    "allow": true,
    "reason": "",
    "rule": "default",
    "requires_approval": false,
    "approval_reason": ""
}

# EXAMPLE RULES (customize for your factory):

# High-risk operations require approval
allow := {
    "allow": true,
    "requires_approval": true,
    "approval_reason": "modifying CI/CD workflows requires approval",
    "rule": "high-risk-path"
} if {
    input.tool_name in ["write_file", "edit_file"]
    input.rel_path != null
    regex.match(`^\.github/workflows/`, input.rel_path)
}

# Deployments always need approval
allow := {
    "allow": true,
    "requires_approval": true,
    "approval_reason": "deployment operations require approval",
    "rule": "deployment"
} if {
    input.tool_name == "run_command"
    regex.match(`(deploy|kubectl|terraform apply)`, input.tool_args.command)
}

# Database migrations need HIGH risk tier + approval
allow := {
    "allow": false,
    "reason": "database migrations require HIGH risk tier Work Order",
    "rule": "db-migration-risk-tier"
} if {
    input.tool_name in ["write_file", "edit_file"]
    input.rel_path != null
    regex.match(`migrations/`, input.rel_path)
    input.risk_tier != "HIGH"
}

allow := {
    "allow": true,
    "requires_approval": true,
    "approval_reason": "database migrations require approval",
    "rule": "db-migration-approval"
} if {
    input.tool_name in ["write_file", "edit_file"]
    input.rel_path != null
    regex.match(`migrations/`, input.rel_path)
    input.risk_tier == "HIGH"
}

# Implementer cannot merge (only open PRs)
allow := {
    "allow": false,
    "reason": "implementer role cannot merge pull requests",
    "rule": "separation-of-duties"
} if {
    input.agent == "implementer"
    input.tool_name == "run_command"
    regex.match(`git (push|merge).*main`, input.tool_args.command)
}

# Block dangerous operations while tainted
allow := {
    "allow": false,
    "reason": "cannot run commands while untrusted content is in context",
    "rule": "taint-command-block"
} if {
    input.tainted == true
    input.tool_name == "run_command"
}
