# CLI Tool Configuration Format (Phase 2b)
#
# Tools are defined as YAML files in config/tools/*.yaml
# Each tool declares its interface, command template, and security properties.
#
# Example: config/tools/github.yaml

name: github_create_pr
description: Create a GitHub pull request
category: git_host

parameters:
  type: object
  properties:
    repo:
      type: string
      description: Repository in owner/name format
    title:
      type: string
      description: PR title
    body:
      type: string
      description: PR body
    head:
      type: string
      description: Head branch
    base:
      type: string
      description: Base branch (default main)
  required: [repo, title, head]

# Command template - {param} will be substituted with JSON-safe values
# For complex args, use temporary files: {param:file} creates a temp file
command: |
  gh pr create \
    --repo "{repo}" \
    --title "{title}" \
    --body-file {body:file} \
    --head "{head}" \
    --base "{base}"

# Security properties
requires_approval: false  # Set true for high-risk tools
sensitive: false          # Set true to block while tainted context
mutates: true            # Does this tool change external state?

# Credentials (environment variables to inject)
credentials:
  - GITHUB_TOKEN

# Success/failure detection
success_exit_codes: [0]
parse_output: json  # json | text | lines

# Policy hints (used by OPA if configured)
risk_level: medium  # low | medium | high
