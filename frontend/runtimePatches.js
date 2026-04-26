import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const MODULE_DIR = path.dirname(fileURLToPath(import.meta.url));
const SLICE_ANSI_PATH = path.join(MODULE_DIR, 'node_modules', 'slice-ansi', 'index.js');
const SLICE_ANSI_LINK_MARKER = "const linkStartCodePrefix = '\\u001B]8;;';";
const SLICE_ANSI_CONSTANTS_ANCHOR = `for (const [start, end] of ansiStyles.codes) {
\tendCodesSet.add(ansiStyles.color.ansi(end));
\tendCodesMap.set(ansiStyles.color.ansi(start), ansiStyles.color.ansi(end));
}
`;
const SLICE_ANSI_CONSTANTS_PATCH = `for (const [start, end] of ansiStyles.codes) {
\tendCodesSet.add(ansiStyles.color.ansi(end));
\tendCodesMap.set(ansiStyles.color.ansi(start), ansiStyles.color.ansi(end));
}

const linkStartCodePrefix = '\\u001B]8;;';
const linkStartCodePrefixCharCodes = [...linkStartCodePrefix].map(character => character.charCodeAt(0));
const linkCodeSuffix = '\\u0007';
const linkEndCode = linkStartCodePrefix + linkCodeSuffix;
endCodesSet.add(linkEndCode);
`;
const SLICE_ANSI_GET_END_CODE_ANCHOR = `\tif (endCodesMap.has(code)) {
\t\treturn endCodesMap.get(code);
\t}

\tcode = code.slice(2);
`;
const SLICE_ANSI_GET_END_CODE_PATCH = `\tif (endCodesMap.has(code)) {
\t\treturn endCodesMap.get(code);
\t}

\tif (code.startsWith(linkStartCodePrefix)) {
\t\treturn linkEndCode;
\t}

\tcode = code.slice(2);
`;
const SLICE_ANSI_PARSE_ANCHOR = `function parseAnsiCode(string, offset) {
\tstring = string.slice(offset, offset + MAX_ANSI_SEQUENCE_LENGTH);
\tconst startIndex = findNumberIndex(string);
\tif (startIndex !== -1) {
\t\tlet endIndex = string.indexOf('m', startIndex);
\t\tif (endIndex === -1) {
\t\t\tendIndex = string.length;
\t\t}

\t\treturn string.slice(0, endIndex + 1);
\t}
}

`;
const SLICE_ANSI_PARSE_PATCH = `function parseLinkCode(string, offset) {
\tstring = string.slice(offset);
\tfor (let index = 1; index < linkStartCodePrefixCharCodes.length; index++) {
\t\tif (string.charCodeAt(index) !== linkStartCodePrefixCharCodes[index]) {
\t\t\treturn;
\t\t}
\t}

\tconst endIndex = string.indexOf(linkCodeSuffix, linkStartCodePrefix.length);
\tif (endIndex === -1) {
\t\treturn;
\t}

\treturn string.slice(0, endIndex + 1);
}

function parseAnsiCode(string, offset) {
\tstring = string.slice(offset, offset + MAX_ANSI_SEQUENCE_LENGTH);
\tconst startIndex = findNumberIndex(string);
\tif (startIndex !== -1) {
\t\tlet endIndex = string.indexOf('m', startIndex);
\t\tif (endIndex === -1) {
\t\t\tendIndex = string.length;
\t\t}

\t\treturn string.slice(0, endIndex + 1);
\t}
}

`;
const SLICE_ANSI_TOKENIZE_ANCHOR = `\t\tif (ESCAPES.has(codePoint)) {
\t\t\tconst code = parseAnsiCode(string, index);
`;
const SLICE_ANSI_TOKENIZE_PATCH = `\t\tif (ESCAPES.has(codePoint)) {
\t\t\tconst code = parseLinkCode(string, index) || parseAnsiCode(string, index);
`;
function replaceRequired(source, anchor, replacement, label) {
    if (source.includes(replacement)) {
        return source;
    }
    if (!source.includes(anchor)) {
        throw new Error(`Missing ${label} while patching ${SLICE_ANSI_PATH}`);
    }
    return source.replace(anchor, replacement);
}
function ensureSliceAnsiHyperlinkPatch() {
    if (!fs.existsSync(SLICE_ANSI_PATH)) {
        return false;
    }
    const source = fs.readFileSync(SLICE_ANSI_PATH, 'utf8');
    if (source.includes(SLICE_ANSI_LINK_MARKER)) {
        return false;
    }
    let nextSource = source;
    nextSource = replaceRequired(nextSource, SLICE_ANSI_CONSTANTS_ANCHOR, SLICE_ANSI_CONSTANTS_PATCH, 'slice-ansi constants anchor');
    nextSource = replaceRequired(nextSource, SLICE_ANSI_GET_END_CODE_ANCHOR, SLICE_ANSI_GET_END_CODE_PATCH, 'slice-ansi end-code anchor');
    nextSource = replaceRequired(nextSource, SLICE_ANSI_PARSE_ANCHOR, SLICE_ANSI_PARSE_PATCH, 'slice-ansi parse anchor');
    nextSource = replaceRequired(nextSource, SLICE_ANSI_TOKENIZE_ANCHOR, SLICE_ANSI_TOKENIZE_PATCH, 'slice-ansi tokenize anchor');
    if (nextSource !== source) {
        fs.writeFileSync(SLICE_ANSI_PATH, nextSource);
        return true;
    }
    return false;
}
export function applyDashboardRuntimePatches() {
    ensureSliceAnsiHyperlinkPatch();
}
