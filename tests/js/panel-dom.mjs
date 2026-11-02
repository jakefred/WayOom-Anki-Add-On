import { readFileSync } from 'node:fs';

// Minimal explicit DOM double. Browser rendering, tab order, and Qt remain
// manual gates; this checks persistent nodes, values, focus and event wiring.
export function panelDocument() {
  const document = { activeElement: null };
  const elements = new Map();
  function element(tagName = 'div') {
    return { tagName, value: '', textContent: '', hidden: false, disabled: false, children: [], attributes: {}, listeners: {},
      addEventListener(type, callback) { (this.listeners[type] ??= []).push(callback); },
      dispatch(type) { for (const callback of this.listeners[type] ?? []) callback({ preventDefault() {} }); },
      click() { if (!this.disabled) this.dispatch('click'); },
      replaceChildren(...children) { this.children = children; this.replacements = (this.replacements ?? 0) + 1; },
      append(...children) { this.children.push(...children); },
      setAttribute(name, value) { this.attributes[name] = value; },
      focus() { document.activeElement = this; },
    };
  }
  const html = readFileSync(new URL('../../panel.html', import.meta.url), 'utf8');
  for (const match of html.matchAll(/<(\w+)\b[^>]*\bid="([^"]+)"[^>]*>/g)) {
    const node = element(match[1]);
    node.hidden = /\bhidden\b/.test(match[0]);
    node.disabled = /\bdisabled\b/.test(match[0]);
    node.value = /\bvalue="([^"]*)"/.exec(match[0])?.[1] ?? '';
    elements.set(`#${match[2]}`, node);
  }
  elements.get('#difficulty').value = 'intermediate';
  elements.get('#generation-form').reset = () => {
    for (const id of ['destination', 'deck-name', 'topic']) elements.get(`#${id}`).value = '';
    elements.get('#card-count').value = '10'; elements.get('#difficulty').value = 'intermediate';
  };
  document.querySelector = selector => elements.get(selector);
  document.createElement = element;
  return { document, elements: Object.fromEntries(elements) };
}
