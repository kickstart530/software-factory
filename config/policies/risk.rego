# Factory Policy - Risk Tier Rules
#
# This module defines risk tier policies for different paths and operations.

package factory.risk

import future.keywords.if
import future.keywords.in

# Paths that require HIGH risk tier
high_risk_patterns := [
    `^\.github/workflows/`,
    `^infra/`,
    `(^|/)Dockerfile$`,
    `(^|/)migrations/`,
    `^auth/`,
    `^payment/`
]

# Check if a path matches high-risk patterns
is_high_risk_path(path) if {
    some pattern in high_risk_patterns
    regex.match(pattern, path)
}

# Check if operation is high-risk
is_high_risk_operation(tool, args) if {
    tool == "run_command"
    cmd := args.command
    # Deployment commands
    regex.match(`(deploy|kubectl|terraform\s+apply|helm\s+install|helm\s+upgrade)`, cmd)
}

is_high_risk_operation(tool, args) if {
    tool == "run_command"
    cmd := args.command
    # Database operations
    regex.match(`(psql|mysql|mongosh|redis-cli).*(<|DROP|ALTER|CREATE\s+USER)`, cmd)
}
