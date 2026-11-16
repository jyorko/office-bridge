(nodes) => {
  const allowed = new Set([
    'a', 'blockquote', 'br', 'code', 'em', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'hr', 'li', 'ol', 'p', 'pre', 'strong', 'ul'
  ]);
  const voidTags = new Set(['br', 'hr']);
  const maxNodes = 2048;
  const maxDepth = 16;
  const maxTextLength = 2000;
  const maxAttributeLength = 12000;
  const isRecord = value => value !== null && typeof value === 'object' &&
    !Array.isArray(value);

  const safeLink = value => {
    if (typeof value !== 'string' || !/^https?:\/\//i.test(value) ||
        /[\s\u0000-\u001f\u007f]/u.test(value)) return false;
    try {
      const url = new URL(value);
      const authority = value.slice(value.indexOf('://') + 3).split(/[/?#]/, 1)[0];
      const port = url.port ? Number(url.port) : null;
      return (url.protocol === 'http:' || url.protocol === 'https:') &&
        !!url.hostname && !url.username && !url.password && !authority.includes('@') &&
        (port === null || (port >= 1 && port <= 65535));
    } catch {
      return false;
    }
  };
  const exactKeys = (value, expected) => {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
    const keys = Object.keys(value).sort();
    return keys.length === expected.length &&
      keys.every((key, index) => key === expected[index]);
  };

  if (!Array.isArray(nodes) || typeof document === 'undefined') return null;
  const template = document.createElement('template');
  let nodeCount = 0;
  let textLength = 0;
  let attributeLength = 0;
  const append = (parent, list, depth) => {
    if (!Array.isArray(list)) return false;
    for (const node of list) {
      nodeCount += 1;
      if (nodeCount > maxNodes || depth > maxDepth) return false;
      if (exactKeys(node, ['text'])) {
        if (typeof node.text !== 'string' || node.text.includes('\0')) return false;
        textLength += node.text.length;
        if (textLength > maxTextLength) return false;
        parent.appendChild(document.createTextNode(node.text));
        continue;
      }
      if (!exactKeys(node, ['attrs', 'children', 'tag']) ||
          typeof node.tag !== 'string' || !allowed.has(node.tag) ||
          !isRecord(node.attrs) ||
          !Array.isArray(node.children)) return false;

      const names = Object.keys(node.attrs);
      const permitted = node.tag === 'a'
        ? new Set(['href', 'title'])
        : node.tag === 'ol' ? new Set(['start']) : new Set();
      if (names.some(name => !permitted.has(name))) return false;
      if (node.tag === 'a' &&
          (!Object.prototype.hasOwnProperty.call(node.attrs, 'href') ||
           !safeLink(node.attrs.href))) return false;
      if (node.tag === 'ol' && Object.prototype.hasOwnProperty.call(node.attrs, 'start') &&
          (typeof node.attrs.start !== 'string' || !/^\d+$/.test(node.attrs.start))) return false;
      for (const name of names) {
        const value = node.attrs[name];
        if (typeof value !== 'string' || value.includes('\0')) return false;
        attributeLength += name.length + value.length;
        if (attributeLength > maxAttributeLength) return false;
      }

      const element = document.createElement(node.tag);
      for (const name of names) element.setAttribute(name, node.attrs[name]);
      parent.appendChild(element);
      if (!voidTags.has(node.tag) && !append(element, node.children, depth + 1)) return false;
      if (voidTags.has(node.tag) && node.children.length !== 0) return false;
    }
    return true;
  };

  try {
    return append(template.content, nodes, 1) ? template : null;
  } catch {
    return null;
  }
}
