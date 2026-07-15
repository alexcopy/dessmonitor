"""Manual control queue boundary for dessmonitor.

Provides passive types and pure functions to enqueue and cancel manual
control commands — without executing hardware, creating command proposals,
or wiring into runtime.

This is a queue boundary, not hardware execution:
  - manual command object (ManualControlCommand)
  - queue item (ManualControlQueueItem)
  - immutable snapshot (ManualControlQueueSnapshot)
  - enqueue helper (enqueue_manual_control_command)
  - cancel helper (cancel_manual_control_command)
  - duplicate/idempotency protection

No API endpoints. No CommandProposal. No CommandQueue class. No executor.
No runtime wiring. No hardware calls. All timestamps are caller-provided.

This module uses only:
  - Python standard library (dataclasses, enum, field)
  - app.control.domain types: DesiredState
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from app.control.domain import DesiredState

# ---------------------------------------------------------------------------
# ManualControlStatus — terminal and non-terminal queue statuses
# ---------------------------------------------------------------------------

class ManualControlStatus(Enum):
    """Status of a manual control queue item.

    QUEUED is the only non-terminal status.
    CANCELLED, REJECTED, EXPIRED are terminal.
    """
    QUEUED = "queued"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"

# ---------------------------------------------------------------------------
# Terminal status set for reuse in enqueue/cancel logic
# ---------------------------------------------------------------------------

_TERMINAL_STATUSES = frozenset({
    ManualControlStatus.CANCELLED,
    ManualControlStatus.REJECTED,
    ManualControlStatus.EXPIRED,
})

# ---------------------------------------------------------------------------
# Type 1: ManualControlCommand
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ManualControlCommand:
    """A manual control intent from a human operator.

    Pure data — no hardware execution, no side effects.
    created_at is a caller-provided string, not a system-clock-generated timestamp.
    """
    command_id: str
    load_id: str
    desired_state: DesiredState
    source: str = "manual"
    requested_by: str = ""
    reason: str = ""
    idempotency_key: str = ""
    created_at: str = ""

# ---------------------------------------------------------------------------
# Type 2: ManualControlQueueItem
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ManualControlQueueItem:
    """An item in the manual control queue with status tracking.

    Pure data — no hardware execution, no side effects.
    updated_at is a caller-provided string, not a system-clock-generated timestamp.
    """
    command: ManualControlCommand
    status: ManualControlStatus = ManualControlStatus.QUEUED
    blocked_by: tuple[str, ...] = field(default_factory=tuple)
    safety_notes: str = ""
    updated_at: str = ""

# ---------------------------------------------------------------------------
# Type 3: ManualControlQueueSnapshot
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ManualControlQueueSnapshot:
    """A snapshot of all items in the manual control queue.

    Pure data — no side effects.
    """
    items: tuple[ManualControlQueueItem, ...] = field(default_factory=tuple)

# ---------------------------------------------------------------------------
# Type 4: ManualControlQueueResult
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ManualControlQueueResult:
    """Result of a manual control queue operation.

    accepted: whether the operation was accepted.
    snapshot: the new queue snapshot after the operation.
    item: the affected queue item (if any).
    reason: human-readable reason string.
    blocked_by: blocking condition identifiers (if rejected).
    """
    accepted: bool = False
    snapshot: ManualControlQueueSnapshot | None = None
    item: ManualControlQueueItem | None = None
    reason: str = ""
    blocked_by: tuple[str, ...] = field(default_factory=tuple)

# ===================================================================
# enqueue_manual_control_command — pure enqueue with idempotency
# ===================================================================

def enqueue_manual_control_command(
    snapshot: ManualControlQueueSnapshot | None,
    command: ManualControlCommand,
) -> ManualControlQueueResult:
    """Enqueue a manual control command.

    Pure, deterministic, side-effect-free. Checks for:
      - empty command_id
      - empty load_id
      - duplicate command_id among non-terminal items
      - duplicate idempotency_key among non-terminal items (if non-empty)

    Args:
        snapshot: Current queue snapshot (None treated as empty).
        command: ManualControlCommand to enqueue.

    Returns:
        ManualControlQueueResult with accepted flag, new snapshot, and reason.
    """
    if snapshot is None:
        snapshot = ManualControlQueueSnapshot()

    # 1. Empty command_id
    if not command.command_id or not command.command_id.strip():
        return ManualControlQueueResult(
            accepted=False,
            snapshot=snapshot,
            reason="empty-command-id",
            blocked_by=("empty-command-id",),
        )

    # 2. Empty load_id
    if not command.load_id or not command.load_id.strip():
        return ManualControlQueueResult(
            accepted=False,
            snapshot=snapshot,
            reason="empty-load-id",
            blocked_by=("empty-load-id",),
        )

    # 3. Check non-terminal items for duplicates
    non_terminal = [
        item for item in snapshot.items
        if item.status not in _TERMINAL_STATUSES
    ]

    # Duplicate command_id
    for item in non_terminal:
        if item.command.command_id == command.command_id:
            return ManualControlQueueResult(
                accepted=False,
                snapshot=snapshot,
                reason="duplicate-command-id",
                blocked_by=("duplicate-command-id",),
            )

    # Duplicate idempotency_key (only if non-empty)
    if command.idempotency_key and command.idempotency_key.strip():
        for item in non_terminal:
            if item.command.idempotency_key and item.command.idempotency_key == command.idempotency_key:
                return ManualControlQueueResult(
                    accepted=False,
                    snapshot=snapshot,
                    reason="duplicate-idempotency-key",
                    blocked_by=("duplicate-idempotency-key",),
                )

    # 4. Accept — create new item and append
    new_item = ManualControlQueueItem(
        command=command,
        status=ManualControlStatus.QUEUED,
    )
    new_items = snapshot.items + (new_item,)
    new_snapshot = ManualControlQueueSnapshot(items=new_items)

    return ManualControlQueueResult(
        accepted=True,
        snapshot=new_snapshot,
        item=new_item,
        reason="queued",
    )

# ===================================================================
# cancel_manual_control_command — pure cancel with terminal guard
# ===================================================================

def cancel_manual_control_command(
    snapshot: ManualControlQueueSnapshot | None,
    command_id: str,
    reason: str = "cancelled",
) -> ManualControlQueueResult:
    """Cancel a queued manual control command.

    Pure, deterministic, side-effect-free.
    - If command_id not found → rejected with reason "command-not-found".
    - If item is already terminal → rejected with reason "command-already-terminal".
    - Otherwise → new snapshot with item status CANCELLED.

    Args:
        snapshot: Current queue snapshot (None treated as empty).
        command_id: The command_id to cancel.
        reason: Cancellation reason (caller-provided).

    Returns:
        ManualControlQueueResult with accepted flag, new snapshot, and reason.
    """
    if snapshot is None:
        snapshot = ManualControlQueueSnapshot()

    # Find the first item with matching command_id
    target_index = None
    target_item = None
    for i, item in enumerate(snapshot.items):
        if item.command.command_id == command_id:
            target_index = i
            target_item = item
            break

    # Not found
    if target_item is None:
        return ManualControlQueueResult(
            accepted=False,
            snapshot=snapshot,
            reason="command-not-found",
        )

    # Already terminal
    if target_item.status in _TERMINAL_STATUSES:
        return ManualControlQueueResult(
            accepted=False,
            snapshot=snapshot,
            reason="command-already-terminal",
            item=target_item,
        )

    # Cancel it — create new item with CANCELLED status
    cancelled_item = ManualControlQueueItem(
        command=target_item.command,
        status=ManualControlStatus.CANCELLED,
        blocked_by=target_item.blocked_by,
        safety_notes=f"Cancelled: {reason}",
        updated_at=reason,
    )

    # Build new items tuple with the cancelled item in place
    new_items = list(snapshot.items)
    new_items[target_index] = cancelled_item

    new_snapshot = ManualControlQueueSnapshot(items=tuple(new_items))

    return ManualControlQueueResult(
        accepted=True,
        snapshot=new_snapshot,
        item=cancelled_item,
        reason="cancelled",
    )
