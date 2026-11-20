/* A progressive view over the already-authorized, paginated provider rows.
   The source list remains available without JavaScript and exposes failures. */
(() => {
    const calendar = document.querySelector('[data-task-calendar]');
    if (!calendar) return;
    const grid = calendar.querySelector('[data-calendar-grid]');
    const heading = calendar.querySelector('[data-calendar-month]');
    const entries = [];
    document.querySelectorAll('[data-calendar-provider]').forEach(provider => {
        provider.querySelectorAll('[data-calendar-item]').forEach(row => {
            const titleNode = row.querySelector('[data-calendar-link], [data-calendar-title]');
            const time = row.querySelector('time');
            const when = time && time.getAttribute('datetime');
            if (!titleNode || !when || !/^\d{4}-\d{2}-\d{2}(?:T|$)/.test(when)) return;
            let date = when.slice(0, 10);
            let timeLabel = '';
            if (when.includes('T')) {
                const instant = new Date(when);
                if (Number.isNaN(instant.getTime())) return;
                const local = new Intl.DateTimeFormat('en-GB', { timeZone: calendar.dataset.timezone,
                    year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).formatToParts(instant);
                const part = type => local.find(p => p.type === type).value;
                date = `${part('year')}-${part('month')}-${part('day')}`;
                timeLabel = `${part('hour')}:${part('minute')}`;
            }
            entries.push({ date, time: timeLabel,
                title: titleNode.textContent.trim(), href: titleNode.getAttribute('href'), kind: provider.dataset.calendarProvider });
        });
    });
    const parts = new Intl.DateTimeFormat('en-CA', { timeZone: calendar.dataset.timezone,
        year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(new Date());
    const get = type => parts.find(part => part.type === type).value;
    const current = `${get('year')}-${get('month')}-${get('day')}`;
    let year = Number(get('year'));
    let month = Number(get('month')) - 1;
    const pad = n => String(n).padStart(2, '0');
    const draw = () => {
        heading.textContent = new Intl.DateTimeFormat('ru-RU', { month: 'long', year: 'numeric', timeZone: 'UTC' })
            .format(new Date(Date.UTC(year, month, 1)));
        grid.replaceChildren();
        ['пн', 'вт', 'ср', 'чт', 'пт', 'сб', 'вс'].forEach(label => {
            const day = document.createElement('div');
            day.className = 'task-calendar-weekday';
            day.textContent = label;
            grid.append(day);
        });
        const first = new Date(Date.UTC(year, month, 1));
        const start = (first.getUTCDay() + 6) % 7;
        const count = new Date(Date.UTC(year, month + 1, 0)).getUTCDate();
        const cells = Math.ceil((start + count) / 7) * 7;
        for (let i = 0; i < cells; i++) {
            const date = new Date(Date.UTC(year, month, i - start + 1));
            const iso = `${date.getUTCFullYear()}-${pad(date.getUTCMonth() + 1)}-${pad(date.getUTCDate())}`;
            const cell = document.createElement('div');
            cell.className = 'task-calendar-day';
            if (date.getUTCMonth() !== month) cell.classList.add('is-outside');
            if (iso === current) cell.classList.add('is-today');
            const number = document.createElement('time');
            number.dateTime = iso;
            number.textContent = date.getUTCDate();
            cell.append(number);
            entries.filter(entry => entry.date === iso).forEach(entry => {
                const entryNode = entry.href ? document.createElement('a') : document.createElement('span');
                if (entry.href) entryNode.href = entry.href;
                entryNode.className = `task-calendar-entry task-calendar-entry--${entry.kind}`;
                entryNode.textContent = `${entry.time ? entry.time + ' ' : ''}${entry.title}`;
                cell.append(entryNode);
            });
            grid.append(cell);
        }
    };
    const shift = amount => {
        const next = new Date(Date.UTC(year, month + amount, 1));
        year = next.getUTCFullYear();
        month = next.getUTCMonth();
        draw();
    };
    calendar.querySelector('[data-calendar-prev]').addEventListener('click', () => shift(-1));
    calendar.querySelector('[data-calendar-next]').addEventListener('click', () => shift(1));
    calendar.querySelector('[data-calendar-today]').addEventListener('click', () => {
        year = Number(get('year')); month = Number(get('month')) - 1; draw();
    });
    draw();
    calendar.hidden = false;
})();
