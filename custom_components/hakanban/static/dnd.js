// Drag & drop wiring — extracted from board-view.js.
// HTML5 DnD can't read dataTransfer during dragover, so we keep module-level
// drag state and expose helpers for the board component to call.

let DRAG = null; // { kind: 'card'|'col', id, from? }

// Find the card element before which a dropped card should be inserted.
function cardAfter(container, y) {
  const els = [...container.querySelectorAll(".hk-card:not(.dragging)")];
  let best = { offset: -Infinity, el: null };
  for (const el of els) {
    const box = el.getBoundingClientRect();
    const offset = y - box.top - box.height / 2;
    if (offset < 0 && offset > best.offset) best = { offset, el };
  }
  return best.el;
}

// Index at which a dragged column should be inserted. `centers` are the horizontal
// midpoints of the *other* columns, left to right: the server removes the dragged
// column before inserting it, so it must not be counted here either.
export function columnDropIndex(centers, x) {
  for (let i = 0; i < centers.length; i++) {
    if (x < centers[i]) return i;
  }
  return centers.length;
}

// Position at which a dropped card should be inserted into a column. The server
// indexes the column's *full* card list, but a search or label filter leaves only some
// cards in the DOM, so the position is worked out against the full list.
//   fullIds    every card id in the column, in order, without the dragged card
//   visibleIds the ids currently shown, in order, without the dragged card
//   afterId    the visible card the drop landed before (null = after the last one)
export function cardDropPosition(fullIds, visibleIds, afterId) {
  if (afterId) {
    const i = fullIds.indexOf(afterId);
    return i < 0 ? fullIds.length : i;
  }
  if (visibleIds.length) {
    const i = fullIds.indexOf(visibleIds[visibleIds.length - 1]);
    return i < 0 ? fullIds.length : i + 1;
  }
  return fullIds.length;
}

// Wire up all drag & drop handlers on the board's shadow root.
// `api` is the HakanbanApi instance, `boardId` is the current board id,
// and `boardEl` is the .hk-board element. `onJustDragged` is called after
// a card drag ends so the click handler can suppress the modal open, and
// `columnCardIds(colId)` returns every card id in a column (filtered or not).
export function wireDnD(root, api, boardId, boardEl, onJustDragged, columnCardIds) {
  // --- card drag start/end ---
  root.querySelectorAll(".hk-card").forEach((el) => {
    el.addEventListener("dragstart", (e) => {
      DRAG = { kind: "card", id: el.dataset.card, from: el.dataset.col };
      el.classList.add("dragging");
      e.dataTransfer.effectAllowed = "move";
      e.dataTransfer.setData("text/plain", el.dataset.card);
    });
    el.addEventListener("dragend", () => {
      el.classList.remove("dragging");
      DRAG = null;
      onJustDragged();
    });
  });

  // --- card drop targets (each column) ---
  root.querySelectorAll(".hk-col").forEach((colEl) => {
    const colId = colEl.dataset.col;
    const list = colEl.querySelector(".hk-cards");
    colEl.addEventListener("dragover", (e) => {
      if (DRAG?.kind !== "card") return;
      e.preventDefault();
      colEl.classList.add("dragover");
    });
    colEl.addEventListener("dragleave", () => colEl.classList.remove("dragover"));
    colEl.addEventListener("drop", (e) => {
      if (DRAG?.kind !== "card") return;
      e.preventDefault();
      colEl.classList.remove("dragover");
      const after = cardAfter(list, e.clientY);
      const fullIds = columnCardIds(colId).filter((id) => id !== DRAG.id);
      const visibleIds = [...list.querySelectorAll(".hk-card")]
        .map((n) => n.dataset.card)
        .filter((id) => id !== DRAG.id);
      api.moveCard(DRAG.id, colId, cardDropPosition(fullIds, visibleIds, after ? after.dataset.card : null));
    });
  });

  // --- column reordering ---
  root.querySelectorAll("[data-colhead]").forEach((head) => {
    head.addEventListener("dragstart", (e) => {
      DRAG = { kind: "col", id: head.dataset.colhead };
      e.dataTransfer.effectAllowed = "move";
      e.stopPropagation();
    });
    head.addEventListener("dragend", () => (DRAG = null));
  });
  boardEl.addEventListener("dragover", (e) => {
    if (DRAG?.kind !== "col") return;
    e.preventDefault();
  });
  boardEl.addEventListener("drop", (e) => {
    if (DRAG?.kind !== "col") return;
    e.preventDefault();
    const centers = [...boardEl.querySelectorAll(".hk-col")]
      .filter((c) => c.dataset.col !== DRAG.id)
      .map((c) => {
        const box = c.getBoundingClientRect();
        return box.left + box.width / 2;
      });
    api.moveColumn(boardId, DRAG.id, columnDropIndex(centers, e.clientX));
  });
}
