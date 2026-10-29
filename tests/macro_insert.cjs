// Ejecuta el aplicador real de macros sin depender de un navegador o de servicios.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../resources/static/tickets/create_ticket.js'), 'utf8');
const start = source.indexOf('        function applyMacro(m)');
const end = source.indexOf('        function closeMacroPanel()', start);
assert(start >= 0 && end > start);
function editor(text, selection = null) {
    const state = {text, inserts: [], formats: ['bold', 'inline-image'], focused: false};
    state.quill = {
        getSelection: () => selection,
        getLength: () => state.text.length,
        insertText(index, text, origin) { state.text = state.text.slice(0,index) + text + state.text.slice(index); state.inserts.push({index,text,origin}); },
        setSelection(index) { state.cursor = index; },
        focus() { state.focused = true; },
    };
    return state;
}
function apply(state, fallback) {
    const context = {
        rootEl: {querySelector: selector => selector === '#quill-editor' ? (state ? {__quillComposer:state} : null) : fallback},
        closeMacroPanel() {}, Event: class {constructor(type) {this.type=type;}},
    };
    vm.createContext(context);
    vm.runInContext(source.slice(start,end) + ';this.apply = applyMacro;', context);
    return context.apply;
}
const draft = editor('Mi borrador\n');
const richApply = apply(draft);
richApply({actions:{comment:'Primera macro'}});
richApply({actions:{comment:'Segunda macro'}});
assert(draft.text.startsWith('Mi borrador'));
assert(draft.text.includes('Primera macro') && draft.text.includes('Segunda macro'));
assert.deepEqual(draft.formats, ['bold','inline-image']);
assert(draft.inserts.every(operation => operation.origin === 'user'));
assert(draft.focused);
const middle = editor('Antes y después\n',{index:6});
apply(middle)({actions:{comment:'Macro'}});
assert.equal(middle.inserts[0].index,6);
assert(middle.text.includes('Antes ') && middle.text.includes('y después'));
const empty = editor('\n');
apply(empty)({actions:{comment:'Hola'}});
assert.equal(empty.inserts[0].index,0);
const fallback={value:'Borrador plano',dispatchEvent(event){this.event=event.type;},focus(){}};
apply(null,fallback)({actions:{comment:'Macro'}});
assert.equal(fallback.value,'Borrador plano\nMacro');
assert.equal(fallback.event,'input');
const untouched = editor('Sin cambios\n');
apply(untouched)({actions:{}});
assert.equal(untouched.inserts.length,0);
console.log('OK: macros acumulativas, inserción en cursor, editor vacío y alternativa de texto plano.');
