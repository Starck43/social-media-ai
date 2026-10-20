/* Shared cron editor for task creation and editing.
 * Only builder-supported schedules are offered in the UI; existing
 * unsupported expressions fall back to a daily 09:00 default once the
 * operator touches the schedule controls.
 */
(function installTaskScheduleBuilder() {
    const pad = (value) => String(value).padStart(2, "0");
    const toCron = (hour, minute, day, weekday) => `${pad(minute)} ${pad(hour)} ${day} * ${weekday}`;
    const intervalOptions = [1, 2, 3, 4, 6, 8, 12];

    function parseCron(cron) {
        const rawCron = cron || "";
        if (rawCron === "@once") return { isOnce: true, freq: "daily", time: "09:00", weekdays: [1], monthday: 1, everyHours: 6 };
        const parts = rawCron.trim().split(/\s+/);
        if (parts.length !== 5) return null;
        const [minute, hour, day, month, weekday] = parts;
        const hourNum = Number(hour);
        const minuteNum = Number(minute);
        const validClock = Number.isInteger(hourNum) && hourNum >= 0 && hourNum <= 23
            && Number.isInteger(minuteNum) && minuteNum >= 0 && minuteNum <= 59;
        const base = { isOnce: false, time: `${pad(validClock ? hourNum : 9)}:${pad(validClock ? minuteNum : 0)}`, weekdays: [1], monthday: 1, everyHours: 6 };
        if (validClock && day === "*" && month === "*" && weekday === "*") {
            return { ...base, freq: "daily" };
        }
        if (validClock && day === "*" && month === "*" && /^\d+(,\d+)*$/.test(weekday)) {
            const values = weekday.split(",").map(Number);
            if (values.every((value) => value >= 0 && value <= 7)) {
                return { ...base, freq: "weekly", weekdays: [...new Set(values.map((value) => value === 0 ? 7 : value))] };
            }
        }
        if (validClock && /^\d+$/.test(day) && Number(day) >= 1 && Number(day) <= 31 && month === "*" && weekday === "*") {
            return { ...base, freq: "monthly", monthday: Number(day) };
        }
        const interval = hour.match(/^\*\/(\d+)$/);
        if (minute === "0" && interval && day === "*" && month === "*" && weekday === "*"
            && intervalOptions.includes(Number(interval[1]))) {
            return { ...base, freq: "interval", everyHours: Number(interval[1]) };
        }
        return null;
    }

    function notifyOnce(target) {
        if (target && typeof target.dispatchEvent === "function") {
            target.dispatchEvent(new CustomEvent("schedule-once", { detail: Boolean(this.isOnce), bubbles: true }));
        }
    }

    window.taskScheduleBuilder = function taskScheduleBuilder(initialCron = "") {
        const parsed = parseCron(initialCron);
        return {
            originalCron: initialCron,
            rawCron: initialCron,
            dirty: !initialCron,
            time: parsed?.time || "09:00",
            freq: parsed?.freq || "daily",
            isOnce: parsed?.isOnce || false,
            weekdays: parsed?.weekdays || [1],
            monthday: parsed?.monthday || 1,
            everyHours: parsed?.everyHours || 6,
            days: [
                { value: 1, label: "Пн" }, { value: 2, label: "Вт" }, { value: 3, label: "Ср" },
                { value: 4, label: "Чт" }, { value: 5, label: "Пт" }, { value: 6, label: "Сб" },
                { value: 7, label: "Вс" },
            ],
            intervalOptions,
            schedNotifyEl: null,
            notifyOnce() {
                notifyOnce.call(this, this.schedNotifyEl);
            },
            load(cron) {
                const value = cron || "";
                const parsedValue = parseCron(value);
                this.originalCron = value;
                this.rawCron = value;
                this.dirty = false;
                Object.assign(this, parsedValue || { isOnce: false, freq: "daily", time: "09:00", weekdays: [1], monthday: 1, everyHours: 6 });
                this.notifyOnce();
            },
            get cron() {
                if (!this.dirty && this.originalCron) return this.originalCron;
                if (this.isOnce) return "@once";
                const [hour, minute] = this.time.split(":");
                switch (this.freq) {
                    case "weekly":
                        return toCron(hour, minute, "*", this.weekdays.slice().sort((a, b) => a - b).join(","));
                    case "monthly":
                        return toCron(hour, minute, this.monthday, "*");
                    case "interval":
                        return `0 */${this.everyHours} * * *`;
                    default:
                        return toCron(hour, minute, "*", "*");
                }
            },
            presetCron(key) {
                if (key === "daily") return toCron(9, 0, "*", "*");
                if (key === "weekly") return toCron(9, 0, "*", "1");
                if (key === "every_6_hours") return "0 */6 * * *";
                if (key === "once") return "@once";
                return null;
            },
            isPreset(key) {
                return this.cron === this.presetCron(key);
            },
            markChanged() {
                this.dirty = true;
            },
            applyPreset(key) {
                this.dirty = true;
                this.isOnce = false;
                if (key === "daily") {
                    this.freq = "daily";
                    this.time = "09:00";
                } else if (key === "weekly") {
                    this.freq = "weekly";
                    this.time = "09:00";
                    this.weekdays = [1];
                } else if (key === "every_6_hours") {
                    this.freq = "interval";
                    this.everyHours = 6;
                } else if (key === "once") {
                    this.isOnce = true;
                }
                this.notifyOnce();
            },
            toggleDay(value) {
                this.dirty = true;
                const index = this.weekdays.indexOf(value);
                if (index === -1) this.weekdays.push(value);
                else this.weekdays.splice(index, 1);
                if (!this.weekdays.length) this.weekdays.push(value);
            },
        };
    };
})();
