"""
sgt_lab_dialog.py — the SGT lab (Settings -> Tracker -> "SGT lab")
====================================================================
Blueprint 14.4 step 11 / 14.10 W12-2. Shows the miner's proposals (`core/sgt_i/miner.py`,
`~/AmanAssociates_Sera/sgt_i/proposals.json`) as cards: container, page kind, type, support,
proven rules, drafted spec, replay diff. **Accept** writes the drafted spec into the local
override `sgt_fields.json` (`core/sgt_i/lab.py`); a developer later promotes it into the
shipped file. **Reject** only remembers the decision — nothing is written to the override
file. All decision logic lives in `core/sgt_i/lab.py` and is tested there; this file is the
Qt shell around it.

Decision 2026-09-28: a newly accepted datapoint stays in the lab and the override file only —
no tracker column is added here. A developer wires a column by hand once it has proven itself.
"""

from __future__ import annotations

import json
import threading
from typing import Any, Dict, Optional

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from core.sgt.sgt_toolbox import SUBMIT_LEVELS
from core.sgt_i import lab, miner
from core.sgt_i.residues import ResidueCounts

BLIND_SPOTS_SHOWN = 8

try:
    import qtawesome as qta
except Exception:
    qta = None


def _icon(name: str, color: str = "#F8FAFC"):
    if qta is not None:
        try:
            return qta.icon(name, color=color)
        except Exception:
            pass
    from PySide6.QtGui import QIcon
    return QIcon()


_SECTION_LABEL = {miner.PROFILE: "Client profile", miner.CURRENT: "Current dataset", miner.RECORDS: "Record list"}


def _support_text(support: Dict[str, Any]) -> str:
    parts = []
    if "clients" in support:
        parts.append(f"{support['clients']} client(s)")
    if "seen" in support:
        parts.append(f"seen {support['seen']}x")
    if "fields" in support:
        parts.append(f"{support['fields']} field(s)")
    if "wrong" in support:
        parts.append(f"wrong {support['wrong']}, unclear {support.get('unknown', 0)}")
    return ", ".join(parts) or "—"


def _rules_text(rules: Dict[str, Any]) -> str:
    if not rules:
        return "none proven"
    return ", ".join(f"{k}={v}" for k, v in rules.items())


def _replay_text(replay: Optional[Dict[str, Any]]) -> str:
    if not replay:
        return "not replayed"
    bits = [f"{replay.get('pages', 0)} page(s)", f"+{replay.get('adds', 0)} new capture(s)"]
    if replay.get("changed") or replay.get("removed") or replay.get("held"):
        bits.append(f"changed {replay.get('changed', 0)}, removed {replay.get('removed', 0)}, "
                     f"held {replay.get('held', 0)}")
    return ", ".join(bits)


def _default_field(proposal: Dict[str, Any]) -> str:
    spec = proposal.get("spec") or {}
    if proposal.get("section") == miner.RECORDS:
        fields = spec.get("fields") or []
        return str(fields[0].get("field")) if fields else ""
    return str(spec.get("field") or "")


class _ProposalCard(QFrame):
    """One proposal: its evidence, its drafted spec, and Accept / Reject."""

    def __init__(self, proposal: Dict[str, Any], on_decided, parent=None):
        super().__init__(parent)
        self.proposal = proposal
        self._on_decided = on_decided
        self.setObjectName("SgtLabCard")
        self.setStyleSheet(
            "#SgtLabCard { background-color: #141414; border: 1px solid #262626; border-radius: 8px; }"
            "QLabel { color: #E6E6E6; }")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(6)

        title = QLabel(f"{proposal.get('container') or proposal.get('type') or proposal.get('source')}")
        title.setStyleSheet("font-size: 13.5px; font-weight: 700; color: #F8FAFC;")
        title.setWordWrap(True)
        outer.addWidget(title)

        meta = QLabel(
            f"Portal: {proposal.get('portal') or '—'}   ·   Page kind: {proposal.get('kind') or '—'}   ·   "
            f"Type: {proposal.get('type') or '—'}   ·   Section: {_SECTION_LABEL.get(proposal.get('section'), '—')}")
        meta.setStyleSheet("font-size: 11px; color: #9AA0A6;")
        meta.setWordWrap(True)
        outer.addWidget(meta)

        outer.addWidget(self._line(f"Support: {_support_text(proposal.get('support') or {})}"))
        outer.addWidget(self._line(f"Proven rules: {_rules_text(proposal.get('rules') or {})}"))
        outer.addWidget(self._line(f"Replay diff: {_replay_text(proposal.get('replay'))}"
                                    + ("  — CHANGES AN EXISTING CAPTURE" if proposal.get("red") else "")))

        spec = proposal.get("spec")
        if spec is not None:
            spec_box = QLabel(json.dumps(spec, ensure_ascii=False, indent=1))
            spec_box.setStyleSheet("font-family: Consolas, monospace; font-size: 10.5px; color: #C7CCD1; "
                                    "background-color: #0D0D0D; border: 1px solid #262626; border-radius: 6px; "
                                    "padding: 8px;")
            spec_box.setWordWrap(True)
            spec_box.setTextInteractionFlags(Qt.TextSelectableByMouse)
            outer.addWidget(spec_box)
        else:
            outer.addWidget(self._line("No drafted spec — needs: " + ", ".join(proposal.get("needs") or [])))

        controls = QHBoxLayout()
        controls.setSpacing(8)

        self._rename_edit: Optional[QLineEdit] = None
        self._level_combo: Optional[QComboBox] = None

        if spec is not None:
            controls.addWidget(QLabel("Field name:"))
            self._rename_edit = QLineEdit(_default_field(proposal))
            self._rename_edit.setFixedWidth(180)
            controls.addWidget(self._rename_edit)

        if "level" in (proposal.get("needs") or []):
            controls.addWidget(QLabel("Ladder level:"))
            self._level_combo = QComboBox()
            self._level_combo.addItem("(pick one)", None)
            levels_seen = (proposal.get("support") or {}).get("levels_seen") or {}
            default_level = max(levels_seen, key=levels_seen.get) if levels_seen else None
            for lvl in SUBMIT_LEVELS:
                self._level_combo.addItem(lvl, lvl)
            if default_level in SUBMIT_LEVELS:
                self._level_combo.setCurrentIndex(SUBMIT_LEVELS.index(default_level) + 1)
            controls.addWidget(self._level_combo)

        controls.addStretch()

        reject_btn = QPushButton("Reject")
        reject_btn.setIcon(_icon("mdi.close", color="#F8FAFC"))
        reject_btn.clicked.connect(self._on_reject)
        controls.addWidget(reject_btn)

        accept_btn = QPushButton("Accept")
        accept_btn.setProperty("class", "primary")
        accept_btn.setIcon(_icon("mdi.check", color="#FFFFFF"))
        accept_btn.setEnabled(spec is not None)
        accept_btn.clicked.connect(self._on_accept)
        controls.addWidget(accept_btn)

        outer.addLayout(controls)

    def _line(self, text: str) -> QLabel:
        lab_widget = QLabel(text)
        lab_widget.setStyleSheet("font-size: 11.5px; color: #C7CCD1;")
        lab_widget.setWordWrap(True)
        return lab_widget

    def _on_accept(self) -> None:
        needs = self.proposal.get("needs") or []
        level = self._level_combo.currentData() if self._level_combo is not None else None
        if "level" in needs and not level:
            QMessageBox.warning(self, "SGT lab", "Pick a ladder level for this status wording first.")
            return
        rename = (self._rename_edit.text().strip() if self._rename_edit is not None else "") or None
        if rename == _default_field(self.proposal):
            rename = None
        try:
            lab.accept(self.proposal, level=level, rename=rename)
        except Exception as e:
            QMessageBox.warning(self, "SGT lab", f"Could not accept this proposal: {e}")
            return
        self._on_decided(self.proposal["id"], "accepted")

    def _on_reject(self) -> None:
        lab.reject(self.proposal["id"])
        self._on_decided(self.proposal["id"], "rejected")


class SgtLabDialog(QDialog):
    """Settings -> Tracker -> "SGT lab". Lists pending proposals; Accept / Reject act right away
    (`core/sgt_i/lab.py`) and the card disappears from the list."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("SGT lab — new datapoints SGT-I found")
        self.setMinimumSize(760, 560)
        self.resize(820, 640)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(10)

        title = QLabel("SGT lab")
        title.setStyleSheet("font-size: 17px; font-weight: 700; color: #F8FAFC;")
        layout.addWidget(title)

        subtitle = QLabel(
            "SGT-I's suggestions for new datapoints, drawn only from counting and maths (no AI). "
            "Accept writes the drafted spec to this PC's local override file; a developer promotes "
            "it into the shipped one later. Reject only remembers your choice — nothing is written.")
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("font-size: 11.5px; color: #9AA0A6;")
        layout.addWidget(subtitle)

        self._empty_label = QLabel("No pending proposals.")
        self._empty_label.setStyleSheet("font-size: 12px; color: #9AA0A6; padding: 20px;")
        self._empty_label.setAlignment(Qt.AlignCenter)

        self._list_widget = QWidget()
        self._list_layout = QVBoxLayout(self._list_widget)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(10)
        self._list_layout.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._list_widget)
        layout.addWidget(scroll, 1)
        layout.addWidget(self._empty_label)

        # Step 10: where SGT-I is blind - typed values no spec claimed, worst first (counts only)
        blind = ResidueCounts().report()[:BLIND_SPOTS_SHOWN]
        if blind:
            blind_label = QLabel("Not understood yet (seen on pages, claimed by no spec):\n"
                                 + "\n".join(blind))
            blind_label.setWordWrap(True)
            blind_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            blind_label.setStyleSheet("font-size: 11px; color: #9AA0A6;")
            layout.addWidget(blind_label)

        # Portal furniture SGT-I learnt and leaves out of its page maps (read-only, 2026-09-28)
        furniture = lab.furniture_summary()
        if furniture:
            furniture_label = QLabel("Treated as page furniture (menus, headers, footers - ignored by "
                                     "SGT-I, still read by SGT):\n" + "\n".join(furniture))
            furniture_label.setWordWrap(True)
            furniture_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            furniture_label.setStyleSheet("font-size: 11px; color: #9AA0A6;")
            layout.addWidget(furniture_label)

        close_row = QHBoxLayout()
        # Decision 2026-09-28 (W12-R): the miner runs only when asked, from here, off the UI thread.
        self._mine_btn = QPushButton("Look for new datapoints")
        self._mine_btn.setToolTip("Runs the miner over this PC's atlas and recent page recordings "
                                  "(counting and maths only). Can take a minute.")
        self._mine_btn.clicked.connect(self._on_mine)
        close_row.addWidget(self._mine_btn)
        self._mine_status = QLabel("")
        self._mine_status.setStyleSheet("font-size: 11px; color: #9AA0A6;")
        close_row.addWidget(self._mine_status)
        self._mined = _MinerSignal()
        self._mined.done.connect(self._on_mined)
        close_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        close_row.addWidget(close_btn)
        layout.addLayout(close_row)

        self._reload()

    def _reload(self) -> None:
        while self._list_layout.count() > 1:
            item = self._list_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        data = lab.load_proposals()
        pending = [p for p in data.get("proposals") or [] if (p.get("status") or "pending") == "pending"]
        self._empty_label.setVisible(not pending)
        for p in pending:
            card = _ProposalCard(p, self._on_decided)
            self._list_layout.insertWidget(self._list_layout.count() - 1, card)

    def _on_decided(self, proposal_id: str, status: str) -> None:
        self._reload()

    def _on_mine(self) -> None:
        self._mine_btn.setEnabled(False)
        self._mine_status.setText("Looking...")
        signal = self._mined

        def work() -> None:
            try:
                got = lab.run_miner()
                signal.done.emit(f"{got['proposals']} pending proposal(s).")
            except Exception as e:
                signal.done.emit(f"The miner stopped: {e}")

        threading.Thread(target=work, name="sgt-lab-miner", daemon=True).start()

    def _on_mined(self, message: str) -> None:
        self._mine_btn.setEnabled(True)
        self._mine_status.setText(message)
        self._reload()


class _MinerSignal(QObject):
    """Carries the miner's result from its worker thread back to the dialog's (UI) thread."""
    done = Signal(str)
