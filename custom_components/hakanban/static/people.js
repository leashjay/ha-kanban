// Home Assistant people (person.* entities) as card assignees: who they are, and how they look.
// Pure functions over `hass.states` / `hass.user`; no DOM state.

import { escapeHtml } from "./util.js";

// Pseudo-id used by the "Unassigned" filter chip; never stored on a card.
export const UNASSIGNED = "__unassigned__";

// "Alyssa Josephs" -> "AJ", "Cameron" -> "CA", "" -> "?".
export function initialsOf(name) {
  const words = String(name || "").trim().split(/\s+/).filter(Boolean);
  if (!words.length) return "?";
  const letters = (w) => [...w];
  if (words.length === 1) return letters(words[0]).slice(0, 2).join("").toUpperCase();
  return (letters(words[0])[0] + letters(words[1])[0]).toUpperCase();
}

// A stable hue (0-359) per person, so the same person always gets the same colour.
export function hueFor(id) {
  let h = 0;
  for (const ch of String(id)) h = (h * 31 + ch.codePointAt(0)) >>> 0;
  return h % 360;
}

// Avatar pictures come from the person entity; only same-origin paths are ever used.
export function safePicture(url) {
  return typeof url === "string" && /^\/(?!\/)[^\s"'<>\\]*$/.test(url) ? url : "";
}

// Every person Home Assistant knows about, sorted by name.
export function listPeople(hass) {
  const states = hass?.states || {};
  return Object.keys(states)
    .filter((id) => id.startsWith("person."))
    .map((id) => {
      const attrs = states[id]?.attributes || {};
      // Without a display name, "person.joy_josephs" reads as "Joy Josephs".
      const fallback = id.slice("person.".length).replace(/_/g, " ").replace(/(^|\s)\S/g, (c) => c.toUpperCase());
      const name = String(attrs.friendly_name || fallback).trim();
      return {
        id,
        name,
        initials: initialsOf(name),
        picture: safePicture(attrs.entity_picture),
        userId: attrs.user_id || null,
        hue: hueFor(id),
      };
    })
    .sort((a, b) => a.name.localeCompare(b.name));
}

export const peopleById = (people) => Object.fromEntries(people.map((p) => [p.id, p]));

// The person for an id, or a placeholder for someone who has since been removed from HA.
export function personFor(byId, id) {
  return byId[id] || { id, name: id, initials: "?", picture: "", userId: null, hue: hueFor(id), unknown: true };
}

// The person entity belonging to the logged-in Home Assistant user, if there is one.
export function myPersonId(people, hass) {
  const uid = hass?.user?.id;
  return uid ? people.find((p) => p.userId === uid)?.id || null : null;
}

// A round avatar: the person's picture if they have one, otherwise their initials.
export function avatarHtml(person) {
  const hue = Number.isInteger(person.hue) ? person.hue : 0; // a number, so safe in a style attribute
  const title = person.unknown ? `${person.name} (no longer in Home Assistant)` : person.name;
  // The picture sits over the initials, so if it fails to load the initials still show.
  const picture = person.picture ? `<img src="${escapeHtml(person.picture)}" alt="">` : "";
  return `<span class="hk-avatar${person.unknown ? " unknown" : ""}" title="${escapeHtml(title)}" style="background:hsl(${hue} 55% 42%)">${escapeHtml(person.initials)}${picture}</span>`;
}

// A toggle chip (avatar + name) used in the card modal and the filter bar.
export function personChipHtml(person, { selected = false, dataAttr, suffix = "" } = {}) {
  return `<span class="hk-person-chip${selected ? " selected" : ""}" ${dataAttr}="${escapeHtml(person.id)}">${avatarHtml(person)}<span>${escapeHtml(person.name)}${escapeHtml(suffix)}</span></span>`;
}
