"""Temporal client integration for submitting Work Orders as workflows.

This module provides the bridge between GitHub webhooks and Temporal workflows.
Phase 2a: directly starts ExecuteTaskDAGWorkflow with a hand-crafted TaskDAG.
Future phases will invoke the Planner agent first to refine the Work Order into a DAG.
"""

from __future__ import annotations

import logging
from typing import Any

from temporalio.client import Client

logger = logging.getLogger(__name__)


async def submit_work_order_to_temporal(
    client: Client,
    work_order: dict[str, Any],
    task_queue: str = "factory-tasks",
) -> dict[str, Any]:
    """Submit a Work Order to Temporal by starting a workflow.

    Phase 2a: Creates a minimal TaskDAG with one task (Implementer agent).
    Later phases will invoke the Planner agent to create a proper multi-task DAG.

    Args:
        client: Connected Temporal client
        work_order: Work Order dict (from GitHub issue)
        task_queue: Temporal task queue name

    Returns:
        Response dict with workflow_id and status

    Raises:
        Exception if workflow start fails
    """
    work_order_id = work_order["id"]
    workflow_id = f"factory-{work_order_id}"

    # Phase 2a: Create a minimal single-task DAG
    # In production, this would call the Planner agent to decompose the Work Order
    task_dag = {
        "work_order_id": work_order_id,
        "tasks": [
            {
                "id": "impl-1",
                "work_order_id": work_order_id,
                "agent": "implementer",
                "title": work_order["title"],
                "instructions": work_order["description"],
                "acceptance_criteria": work_order.get("acceptance_criteria", []),
                "depends_on": [],
                "branch": None,
                "status": "pending",
            }
        ],
    }

    logger.info(f"Starting Temporal workflow {workflow_id} for Work Order {work_order_id}")

    try:
        # Start the workflow
        handle = await client.start_workflow(
            "execute_task_dag",
            args=[task_dag],
            id=workflow_id,
            task_queue=task_queue,
        )

        logger.info(f"Workflow {workflow_id} started successfully")

        return {
            "status": "submitted",
            "work_order_id": work_order_id,
            "workflow_id": workflow_id,
            "workflow_run_id": handle.first_execution_run_id,
        }

    except Exception as e:
        logger.error(f"Failed to start workflow {workflow_id}: {e}", exc_info=True)
        raise
