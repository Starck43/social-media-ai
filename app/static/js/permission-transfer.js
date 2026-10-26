/* Progressive enhancement: the original select remains the form's sole value source. */
(function () {
    'use strict';
    class FilteredSelect {
        constructor(select) {
            if (!select.multiple || select.dataset.transferReady || select.disabled) return;
            this.select = select;
            const grouped = (select.dataset.filteredSelect || 'permissions') === 'permissions';
            const root = document.createElement('div');
            root.className = 'permission-transfer';
            root.dataset.permissionTransferUi = '';
            root.dataset.filteredSelectUi = grouped ? 'permissions' : 'plain';
            root.setAttribute('role', 'group');
            root.setAttribute('aria-label', select.labels[0]?.textContent.trim() || 'Выбор записей');
            const fieldLabel = select.labels[0];
            const baseId = select.id || `filtered-select-${++FilteredSelect.sequence}`;
            select.id = baseId;
            function readEntries() { return Array.from(select.options, (option, index) => {
                const label = option.label.trim();
                const [code, ...name] = grouped ? label.split(' — ') : [label];
                const resource = code.includes('.') ? code.slice(0, code.lastIndexOf('.')) : 'Прочее';
                return {option, id: `${baseId}-choice-${index}`, label, code, name: name.join(' — '),
                    disabled: option.disabled || option.parentElement.disabled,
                    resource: grouped ? resource : '', group: resource.split('.')[0]};
            }).sort((a, b) => a.resource.localeCompare(b.resource) || a.code.localeCompare(b.code)); }
            let entries = readEntries();
            const marked = {available: new Set(), chosen: new Set()};
            const active = {available: null, chosen: null};
            const nodes = {};
            function element(tag, className, text) {
                const node = document.createElement(tag);
                node.className = className;
                if (text) node.textContent = text;
                return node;
            }
            const help = element('p', 'permission-transfer-help',
                'Двойной щелчок/Enter — перенос, пробел — выделение.');
            help.id = `${baseId}-transfer-help`;
            const caption = element('div', 'permission-transfer-caption');
            if (fieldLabel && grouped) caption.append(fieldLabel);
            caption.append(help);
            const filter = element('div', 'permission-transfer-filter');
            const groupLabel = element('label', '', 'Группа');
            const groupSelect = element('select', 'form-select');
            groupSelect.id = `${baseId}-group`;
            groupLabel.htmlFor = groupSelect.id;
            function updateGroups() {
                const previous = groupSelect.value;
                groupSelect.replaceChildren(new Option('Все группы', ''));
                if (grouped) for (const group of [...new Set(entries.map(entry => entry.group))].sort())
                    groupSelect.add(new Option(group, group));
                groupSelect.value = Array.from(groupSelect.options).some(option => option.value === previous) ? previous : '';
            }
            updateGroups();
            filter.append(groupLabel, groupSelect);
            if (grouped) root.append(filter);
            groupSelect.addEventListener('change', () => {
                for (const side of ['available', 'chosen']) {
                    marked[side].clear();
                    active[side] = null;
                    nodes[side].list.scrollTop = 0;
                    render(side);
                }
            });
            const grid = element('div', 'permission-transfer-grid');
            root.append(grid);
            const status = element('p', 'permission-transfer-status');
            status.setAttribute('role', 'status');
            status.setAttribute('aria-live', 'polite');
            status.setAttribute('aria-atomic', 'true');
            function candidates(side) {
                const words = nodes[side].search.value.trim().toLocaleLowerCase().split(/\s+/);
                return entries.filter(entry => entry.option.selected === (side === 'chosen') &&
                    (!groupSelect.value || entry.group === groupSelect.value) &&
                    words.every(word => entry.label.toLocaleLowerCase().includes(word)));
            }
            function panel(side, title) {
                const section = element('section', 'permission-transfer-panel');
                const heading = element('div', 'permission-transfer-heading', title);
                heading.id = `${baseId}-${side}-heading`;
                const count = element('span', 'permission-transfer-count');
                heading.append(count);
                const search = element('input', 'form-control permission-transfer-search');
                search.id = `${baseId}-${side}-search`;
                search.type = 'search';
                search.placeholder = grouped ? 'Код или название' : 'Название';
                search.setAttribute('aria-label', `Поиск: ${title.toLocaleLowerCase()}`);
                const list = element('ul', 'permission-transfer-list');
                list.id = `${baseId}-${side}-list`;
                list.tabIndex = 0;
                list.setAttribute('role', 'listbox');
                list.setAttribute('aria-multiselectable', 'true');
                list.setAttribute('aria-labelledby', heading.id);
                list.setAttribute('aria-describedby', help.id);
                section.append(heading, search, list);
                nodes[side] = {section, count, search, list};
                search.addEventListener('keydown', event => {
                    if (event.key === 'Enter') event.preventDefault();
                });
                search.addEventListener('input', () => {
                    marked[side].clear();
                    active[side] = null;
                    list.scrollTop = 0;
                    render(side);
                });
                list.addEventListener('click', event => {
                    const row = event.target.closest('[role="option"]');
                    if (!row || row.getAttribute('aria-disabled') === 'true' || select.disabled) return;
                    active[side] = row.dataset.value;
                    if (marked[side].has(row.dataset.value)) marked[side].delete(row.dataset.value);
                    else marked[side].add(row.dataset.value);
                    refreshSelection(side);
                    list.focus({preventScroll: true});
                });
                list.addEventListener('dblclick', event => {
                    const row = event.target.closest('[role="option"]');
                    if (row) move(side, new Set([row.dataset.value]));
                });
                list.addEventListener('keydown', event => {
                    const visible = candidates(side).filter(entry => !entry.disabled);
                    if (!visible.length || select.disabled) return;
                    let index = visible.findIndex(entry => entry.option.value === active[side]);
                    if (index < 0) index = 0;
                    if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
                        event.preventDefault();
                        if (event.key === 'ArrowDown') index = Math.min(visible.length - 1, index + (active[side] ? 1 : 0));
                        if (event.key === 'ArrowUp') index = Math.max(0, index - 1);
                        if (event.key === 'Home') index = 0;
                        if (event.key === 'End') index = visible.length - 1;
                        active[side] = visible[index].option.value;
                        refreshSelection(side, true);
                    } else if (event.key === ' ' || event.key === 'Enter') {
                        event.preventDefault();
                        const value = visible[index].option.value;
                        active[side] = value;
                        if (event.key === 'Enter') move(side, marked[side].size ? marked[side] : new Set([value]));
                        else {
                            if (marked[side].has(value)) marked[side].delete(value);
                            else marked[side].add(value);
                            refreshSelection(side);
                        }
                    } else if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'a') {
                        event.preventDefault();
                        marked[side] = new Set(visible.filter(entry => !entry.disabled).map(entry => entry.option.value));
                        refreshSelection(side);
                    } else if (event.key === 'Escape') {
                        marked[side].clear();
                        refreshSelection(side);
                    }
                });
                return section;
            }
            const available = panel('available', 'Доступные');
            const chosen = panel('chosen', 'Выбранные');
            const controls = element('div', 'permission-transfer-controls');
            function transferButton(side, arrow) {
                const button = element('button', 'btn btn-outline-secondary permission-transfer-arrow');
                const icon = element('span', 'permission-transfer-arrow-icon', arrow);
                const count = element('span', 'permission-transfer-mark-count', '0');
                icon.setAttribute('aria-hidden', 'true');
                count.setAttribute('aria-hidden', 'true');
                button.append(icon, count);
                button.type = 'button';
                button.dataset.transferFrom = side;
                button.setAttribute('aria-controls', `${baseId}-available-list ${baseId}-chosen-list`);
                button.addEventListener('click', () => move(side, marked[side]));
                return button;
            }
            nodes.available.button = transferButton('available', '→');
            nodes.chosen.button = transferButton('chosen', '←');
            controls.append(nodes.available.button, nodes.chosen.button);
            grid.append(available, controls, chosen);
            root.append(caption, status);
            function render(side) {
                const {list, count} = nodes[side];
                const scroll = list.scrollTop;
                const visible = candidates(side);
                const total = entries.filter(entry => entry.option.selected === (side === 'chosen')).length;
                count.textContent = `${visible.length} из ${total}`;
                list.replaceChildren();
                let previousResource = null;
                for (const entry of visible) {
                    if (grouped && previousResource !== entry.resource) {
                        const heading = element('li', 'permission-transfer-group', entry.resource);
                        heading.setAttribute('role', 'presentation');
                        list.append(heading);
                        previousResource = entry.resource;
                    }
                    const row = element('li', 'permission-transfer-option');
                    const text = element('span', 'permission-transfer-option-text');
                    const code = element('span', 'permission-transfer-option-code');
                    const split = grouped ? entry.code.lastIndexOf('.') : -1;
                    if (split >= 0) {
                        code.append(element('span', 'permission-transfer-option-resource', entry.code.slice(0, split + 1)));
                        code.append(element('span', '', entry.code.slice(split + 1)));
                    } else code.textContent = entry.code;
                    text.append(code);
                    if (entry.name) text.append(element('span', 'permission-transfer-option-name', entry.name));
                    row.append(text);
                    row.title = entry.label;
                    row.setAttribute('aria-label', entry.label);
                    row.setAttribute('aria-disabled', String(!!entry.disabled));
                    row.id = entry.id;
                    row.dataset.value = entry.option.value;
                    row.setAttribute('role', 'option');
                    row.setAttribute('aria-selected', String(marked[side].has(entry.option.value)));
                    list.append(row);
                }
                if (!visible.length) {
                    const empty = element('li', 'permission-transfer-empty', total ? 'Ничего не найдено' :
                        (side === 'chosen' ? 'Пока ничего не выбрано' : 'Все записи добавлены'));
                    empty.setAttribute('role', 'presentation');
                    list.append(empty);
                }
                list.scrollTop = scroll;
                refreshSelection(side);
            }
            function refreshSelection(side, reveal = false) {
                const {list, button} = nodes[side];
                const visible = candidates(side);
                list.querySelectorAll('[role="option"]').forEach(row =>
                    row.setAttribute('aria-selected', String(marked[side].has(row.dataset.value))));
                const current = visible.find(entry => entry.option.value === active[side]);
                if (current) list.setAttribute('aria-activedescendant', current.id);
                else list.removeAttribute('aria-activedescendant');
                if (reveal && current) {
                    const row = document.getElementById(current.id);
                    if (row.offsetTop < list.scrollTop) list.scrollTop = row.offsetTop;
                    else if (row.offsetTop + row.offsetHeight > list.scrollTop + list.clientHeight)
                        list.scrollTop = row.offsetTop + row.offsetHeight - list.clientHeight;
                }
                const selectedCount = visible.filter(entry => !entry.disabled && marked[side].has(entry.option.value)).length;
                button.disabled = selectedCount === 0;
                button.querySelector('.permission-transfer-mark-count').textContent = String(selectedCount);
                const label = `${side === 'chosen' ? 'Убрать' : 'Добавить'} отмеченные записи: ${selectedCount}`;
                button.setAttribute('aria-label', label);
                button.title = label;
            }
            function move(side, values) {
                if (select.disabled) return;
                const transferred = candidates(side).filter(entry => !entry.disabled && values.has(entry.option.value));
                if (!transferred.length) return;
                for (const entry of transferred) entry.option.selected = side === 'available';
                marked.available.clear();
                marked.chosen.clear();
                active[side] = null;
                render('available');
                render('chosen');
                status.textContent = `${side === 'available' ? 'Добавлено' : 'Убрано'}: ${transferred.length}. ` +
                    `Всего выбрано: ${select.selectedOptions.length}. Сохраните изменения.`;
                select.dispatchEvent(new Event('change', {bubbles: true}));
            }
            select.after(root);
            render('available');
            render('chosen');
            if (fieldLabel) fieldLabel.htmlFor = nodes.available.search.id;
            select.hidden = true;
            select.dataset.transferReady = 'true';
            function synchronize() {
                entries = readEntries();
                updateGroups();
                marked.available.clear();
                marked.chosen.clear();
                active.available = active.chosen = null;
                root.hidden = select.disabled;
                select.hidden = !select.disabled;
                render('available');
                render('chosen');
            }
            select.addEventListener('change', synchronize);
            // Related-record popups may add/rename options; never fetch new choices here.
            new MutationObserver(synchronize).observe(select, {
                childList: true, subtree: true, characterData: true, attributes: true,
                attributeFilter: ['disabled', 'selected', 'label', 'value']
            });
            select.form?.addEventListener('reset', () => {
                setTimeout(() => {
                    marked.available.clear();
                    marked.chosen.clear();
                    active.available = active.chosen = null;
                    nodes.available.search.value = nodes.chosen.search.value = '';
                    groupSelect.value = '';
                    render('available');
                    render('chosen');
                    status.textContent = 'Исходный выбор восстановлен.';
                }, 0);
            });
        }
    static sequence = 0;
    static init(root = document) {
        root.querySelectorAll('select[data-filtered-select], select[data-permission-transfer]')
            .forEach(select => new FilteredSelect(select));
    }
    }
    window.FilteredSelect = FilteredSelect;
    function start() { FilteredSelect.init(); }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, {once: true});
    else start();
})();
