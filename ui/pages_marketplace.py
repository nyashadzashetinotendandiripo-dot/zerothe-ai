"""Marketplace: browse ready-made Bots by category and add them in one click."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLineEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget

from .api import Api
from .store import Store
from .widgets import Avatar, button, card, chip, clear_layout, label, prop, PageHeader, page_layout

CATEGORIES = ["All", "Engineering", "Sales", "Marketing", "Design", "Personal", "Recruiting & People", "Product", "Operations"]


class MarketplacePage(QWidget):
    openBot = Signal(str)
    toast = Signal(str, str)

    def __init__(self, api: Api, store: Store):
        super().__init__()
        self.api, self.store = api, store
        self.query = ""
        self.category = "All"
        root = page_layout(self, PageHeader(
            "Marketplace",
            "Pick a ready-made Bot by category and add it. Setup is a message: the Bot introduces itself, asks for the access it needs, "
            "and never sends, pays, or publishes without your approval."))
        top = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search by name, creator, or what it does…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._on_search)
        top.addWidget(self.search, 1)
        root.addLayout(top)
        cats = QHBoxLayout()
        cats.setSpacing(8)
        self.cat_buttons: dict[str, QPushButton] = {}
        for c in CATEGORIES:
            b = button(c, primary=(c == "All"), flat=(c != "All"), on=lambda _=False, c=c: self.set_category(c))
            b.setProperty("chiplike", True)
            self.cat_buttons[c] = b
            cats.addWidget(b)
        cats.addStretch(1)
        root.addLayout(cats)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QScrollArea.Shape.NoFrame)
        self.grid_host = QWidget()
        self.grid = QGridLayout(self.grid_host)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(14)
        sc.setWidget(self.grid_host)
        root.addWidget(sc, 1)
        self.empty = label("", muted=True)
        self.empty.hide()
        root.addWidget(self.empty)
        store.botsChanged.connect(self.rebuild)
        store.settingsChanged.connect(self.rebuild)
        self.rebuild()

    # -- filtering ---------------------------------------------------------------
    def _on_search(self, text: str) -> None:
        self.query = (text or "").strip().lower()
        self.rebuild()

    def set_category(self, c: str) -> None:
        self.category = c
        for name, b in self.cat_buttons.items():
            prop(b, "primary", name == c)
            prop(b, "flat", name != c)
        self.rebuild()

    def _matches(self, t: dict) -> bool:
        if self.category != "All" and t.get("category", "Other") != self.category:
            return False
        if not self.query:
            return True
        hay = " ".join(str(t.get(k, "")) for k in ("name", "creator", "blurb", "job", "category")).lower()
        return self.query in hay

    def _installed_count(self, tid: str) -> int:
        return sum(1 for b in self.store.bots if b.get("template") == tid)

    def rebuild(self) -> None:
        clear_layout(self.grid)
        entries = [t for t in self.store.templates if self._matches(t)]
        self.empty.setText("Nothing matches. Clear the search or pick another category." if (self.query or self.category != "All")
                           else "No templates available yet.")
        self.empty.setVisible(not entries)
        for i, t in enumerate(entries):
            self.grid.addWidget(self._card(t), i // 2, i % 2)

    # -- cards -------------------------------------------------------------------
    def _card(self, t: dict) -> QWidget:
        c = card("hover")
        v = QVBoxLayout(c)
        v.setContentsMargins(16, 14, 16, 14)
        v.setSpacing(8)
        head = QHBoxLayout()
        head.setSpacing(12)
        head.addWidget(Avatar(t.get("emoji") or "🤖", 40), 0, Qt.AlignmentFlag.AlignTop)
        namecol = QVBoxLayout()
        namecol.setSpacing(1)
        namecol.addWidget(label(t["name"], h2=True, wrap=False))
        namecol.addWidget(label("by " + (t.get("creator") or "Zerothe Team"), faint=True, wrap=False))
        head.addLayout(namecol, 1)
        head.addWidget(chip(t.get("category") or "Other"), 0, Qt.AlignmentFlag.AlignTop)
        v.addLayout(head)
        v.addWidget(label(t.get("blurb") or t.get("job", ""), muted=True))
        foot = QHBoxLayout()
        foot.setSpacing(8)
        n = self._installed_count(t["id"])
        if n:
            foot.addWidget(chip(f"Installed ×{n}"))
        foot.addStretch(1)
        add = button("Add", primary=True, on=lambda _=False, t=t: self.add(t))
        add.setEnabled(True)
        foot.addWidget(add)
        v.addLayout(foot)
        return c

    # -- add ---------------------------------------------------------------------
    def add(self, t: dict) -> None:
        name = t["name"]
        taken = {b["name"].strip().lower() for b in self.store.bots}
        if name.lower() in taken:
            k = 2
            while f"{name} {k}".lower() in taken:
                k += 1
            name = f"{name} {k}"

        def ok(bot: dict) -> None:
            self.toast.emit(f"{bot['name']} added. Say hello to start setup.", "ok")
            self.store.refresh_all(lambda: self.openBot.emit(bot["id"]))

        def fail(e: str) -> None:
            self.toast.emit(e, "error")

        self.api.post("/api/bots/from_template", {"template": t["id"], "name": name}, ok, fail)
