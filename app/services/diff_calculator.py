"""Change detection — compare current scan against stored mappings.

For each file found by the scanner, determine what kind of sync action is needed
by comparing content_version (rev), metadata_hash, and policy_hash with the
last known state in the document_mapping table.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from enum import Enum

from app.connectors.anyshare.scanner import AnyShareFile
from app.models.document_mapping import SyncDocumentMapping

logger = logging.getLogger(__name__)


class DiffAction(str, Enum):
    CREATE = "transfer_create"
    UPDATE = "transfer_update"
    RENAME = "rename"
    MOVE = "move"
    METADATA_SYNC = "metadata_sync"
    PERMISSION_SYNC = "permission_sync"
    NO_CHANGE = "no_change"
    DELETE_CANDIDATE = "archive_delete"


@dataclass
class FileDiff:
    source_doc_id: str
    source_rev: str
    source_name: str
    source_size: int
    source_parent_gns: str
    action: DiffAction
    old_rev: str = ""
    old_name: str = ""
    old_parent_gns: str = ""
    content_version_new: str = ""
    metadata_hash_new: str = ""
    policy_hash_new: str = ""


class DiffCalculator:
    """Compares current scan with stored state to detect changes."""

    @staticmethod
    def content_version(rev: str) -> str:
        return rev or ""

    @staticmethod
    def metadata_hash(name: str, parent_id: str) -> str:
        raw = f"{name}|{parent_id}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    @staticmethod
    def policy_hash(acl_raw: str) -> str:
        return hashlib.sha256(acl_raw.encode()).hexdigest()[:16]

    def compute_diff(
        self,
        scanned_files: list[AnyShareFile],
        existing_mappings: dict[str, SyncDocumentMapping],  # source_doc_id -> mapping
        missing_threshold: int = 2,
    ) -> list[FileDiff]:
        """Compute what changed.

        *scanned_files*: files found in the current scan.
        *existing_mappings*: previously known files from the state DB.
        Returns a list of diffs, one per changed file.
        """
        diffs: list[FileDiff] = []
        scanned_ids = set()

        for sf in scanned_files:
            scanned_ids.add(sf.id)
            cv_new = self.content_version(sf.rev)
            meta_new = self.metadata_hash(sf.name, sf.parent_gns)
            # policy_hash is computed separately when ACL is actually fetched

            existing = existing_mappings.get(sf.id)

            if existing is None:
                # Brand new file
                diffs.append(FileDiff(
                    source_doc_id=sf.id,
                    source_rev=sf.rev,
                    source_name=sf.name,
                    source_size=sf.size,
                    source_parent_gns=sf.parent_gns,
                    action=DiffAction.CREATE,
                    content_version_new=cv_new,
                    metadata_hash_new=meta_new,
                ))
                continue

            # File exists — check what changed
            old_cv = existing.content_version
            old_meta = existing.metadata_hash
            parent_changed = (sf.parent_gns != "")  # simplified

            content_changed = (cv_new != old_cv and cv_new != "")
            name_changed = (sf.name != existing.source_name)

            if content_changed and name_changed:
                # Both content and name changed
                diffs.append(FileDiff(
                    source_doc_id=sf.id,
                    source_rev=sf.rev,
                    source_name=sf.name,
                    source_size=sf.size,
                    source_parent_gns=sf.parent_gns,
                    action=DiffAction.UPDATE,
                    old_rev=old_cv,
                    old_name=existing.source_name,
                    content_version_new=cv_new,
                    metadata_hash_new=meta_new,
                ))
            elif name_changed:
                diffs.append(FileDiff(
                    source_doc_id=sf.id, source_rev=sf.rev,
                    source_name=sf.name, source_size=sf.size,
                    source_parent_gns=sf.parent_gns,
                    action=DiffAction.RENAME,
                    old_name=existing.source_name,
                    content_version_new=cv_new, metadata_hash_new=meta_new,
                ))
            elif content_changed:
                diffs.append(FileDiff(
                    source_doc_id=sf.id, source_rev=sf.rev,
                    source_name=sf.name, source_size=sf.size,
                    source_parent_gns=sf.parent_gns,
                    action=DiffAction.UPDATE,
                    old_rev=old_cv,
                    content_version_new=cv_new, metadata_hash_new=meta_new,
                ))
            elif meta_new != old_meta:
                diffs.append(FileDiff(
                    source_doc_id=sf.id, source_rev=sf.rev,
                    source_name=sf.name, source_size=sf.size,
                    source_parent_gns=sf.parent_gns,
                    action=DiffAction.METADATA_SYNC,
                    content_version_new=cv_new, metadata_hash_new=meta_new,
                ))
            # else: NO_CHANGE — don't emit a diff

        # Detect deletions: files in DB but not in scan
        for doc_id, mapping in existing_mappings.items():
            if doc_id not in scanned_ids:
                missing = (mapping.missing_count or 0) + 1
                # Only flag as delete candidate after threshold
                if missing >= missing_threshold:
                    diffs.append(FileDiff(
                        source_doc_id=doc_id,
                        source_rev="",
                        source_name=mapping.source_name,
                        source_size=0,
                        source_parent_gns="",
                        action=DiffAction.DELETE_CANDIDATE,
                        old_rev=mapping.content_version,
                    ))

        return diffs
