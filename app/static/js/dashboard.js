/**
 * UNIFIED DASHBOARD UTILITIES
 * Общие JavaScript функции для всех дашбордов
 */

// Global config
const DashboardConfig = {
    API_BASE: "/api/v1/dashboard",
    AUTO_REFRESH_INTERVAL: 5 * 60 * 1000, // 5 minutes
    ANIMATION_DURATION: 300,
}

// Utility Functions
const DashboardUtils = {
    /**
     * Format number with K/M suffixes
     */
    formatNumber(num) {
        if (num >= 1000000) return (num / 1000000).toFixed(1) + "M"
        if (num >= 1000) return (num / 1000).toFixed(1) + "K"
        return num.toString()
    },

    /**
     * Format date to locale string
     */
    formatDate(dateString, options = {}) {
        if (!dateString) return "--"
        const date = new Date(dateString)
        const defaultOptions = {
            year: "numeric",
            month: "short",
            day: "numeric",
            ...options,
        }
        return date.toLocaleDateString("ru-RU", defaultOptions)
    },

    /**
     * Format datetime
     */
    formatDateTime(dateString) {
        return this.formatDate(dateString, {
            hour: "2-digit",
            minute: "2-digit",
        })
    },

    /**
     * Get sentiment class based on score
     */
    getSentimentClass(score) {
        if (score > 0.3) return "sentiment-positive"
        if (score < -0.3) return "sentiment-negative"
        return "sentiment-neutral"
    },

    /**
     * Get sentiment label
     */
    getSentimentLabel(score) {
        if (score > 0.3) return "Позитив"
        if (score < -0.3) return "Негатив"
        return "Нейтрал"
    },

    /**
     * Get sentiment emoji
     */
    getSentimentEmoji(score) {
        if (score > 0.3) return "😊"
        if (score < -0.3) return "😞"
        return "😐"
    },

    /**
     * Get platform icon
     */
    getPlatformIcon(platform) {
        const icons = {
            "vk": "fab fa-vk",
            "telegram": "fab fa-telegram",
            "youtube": "fab fa-youtube",
            "instagram": "fab fa-instagram",
        }
        return icons[platform?.toLowerCase()] || "fas fa-globe"
    },

    /**
     * Get platform color
     */
    getPlatformColor(platform) {
        const colors = {
            "vk": "#4680C2",
            "telegram": "#0088cc",
            "youtube": "#FF0000",
            "instagram": "#E4405F",
        }
        return colors[platform?.toLowerCase()] || "#667eea"
    },

    /**
     * Build source URL
     */
    buildSourceUrl(source) {
        if (!source) return "#"

        // If source has base_url from platform, use it
        if (source.base_url && source.external_id) {
            return `${source.base_url}/${source.external_id}`
        }

        // Fallback to hardcoded URLs
        const baseUrls = {
            "vk": "https://vk.com/",
            "telegram": "https://t.me/",
            "youtube": "https://youtube.com/",
            "instagram": "https://instagram.com/",
        }

        const platform = source.platform || source.platform_type || ""
        const baseUrl = baseUrls[platform.toLowerCase()]
        return baseUrl && source.external_id ? `${baseUrl}${source.external_id}` : "#"
    },

    /**
     * Show loading state
     */
    showLoading(show = true) {
        const spinner = document.getElementById("loading")
        if (spinner) {
            spinner.style.display = show ? "block" : "none"
        }

        const btn = document.querySelector(".refresh-btn")
        if (btn) {
            if (show) {
                btn.classList.add("spinning")
            } else {
                btn.classList.remove("spinning")
            }
        }
    },

    /**
     * Show error message
     */
    showError(message, duration = 5000) {
        const errorEl = document.getElementById("error")
        if (errorEl) {
            const textEl = document.getElementById("error-text")
            if (textEl) textEl.textContent = message

            errorEl.style.display = "block"

            if (duration > 0) {
                setTimeout(() => {
                    errorEl.style.display = "none"
                }, duration)
            }
        }
    },

    /**
     * Update timestamp
     */
    updateTimestamp() {
        const timestampEl = document.getElementById("update-time")
        if (timestampEl) {
            const now = new Date()
            timestampEl.textContent = now.toLocaleTimeString("ru-RU")
        }
    },

    /**
     * Get current filters from form
     */
    getFilters() {
        const filters = {}

        const daysFilter = document.getElementById("days-filter")
        if (daysFilter) filters.days = daysFilter.value

        const sourceFilter = document.getElementById("source-filter")
        if (sourceFilter && sourceFilter.value) filters.source_id = sourceFilter.value

        const scenarioFilter = document.getElementById("scenario-filter")
        if (scenarioFilter && scenarioFilter.value) filters.scenario_id = scenarioFilter.value

        return filters
    },

    /**
     * Build query string from filters
     */
    buildQueryString(filters) {
        const params = new URLSearchParams()
        for (const [key, value] of Object.entries(filters)) {
            if (value !== null && value !== undefined && value !== "") {
                params.append(key, value)
            }
        }
        return params.toString()
    },

    /**
     * Fetch API with error handling
     */
    async fetchAPI(endpoint, filters = {}) {
        try {
            const queryString = this.buildQueryString(filters)
            const url = `${DashboardConfig.API_BASE}${endpoint}${queryString ? "?" + queryString : ""}`

            const response = await fetch(url)

            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`)
            }

            return await response.json()
        } catch (error) {
            console.error(`Error fetching ${endpoint}:`, error)
            this.showError(`Ошибка загрузки данных: ${error.message}`)
            throw error
        }
    },

    /**
     * Debounce function
     */
    debounce(func, wait) {
        let timeout
        return function executedFunction(...args) {
            const later = () => {
                clearTimeout(timeout)
                func(...args)
            }
            clearTimeout(timeout)
            timeout = setTimeout(later, wait)
        }
    },

    /**
     * Copy to clipboard
     */
    async copyToClipboard(text) {
        try {
            await navigator.clipboard.writeText(text)
            return true
        } catch (err) {
            console.error("Failed to copy:", err)
            return false
        }
    },

    /**
     * Download as JSON
     */
    downloadJSON(data, filename = "data.json") {
        const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" })
        const url = URL.createObjectURL(blob)
        const a = document.createElement("a")
        a.href = url
        a.download = filename
        a.click()
        URL.revokeObjectURL(url)
    },
}

// Chart Utilities
const ChartUtils = {
    /**
     * Default chart colors
     */
    colors: {
        positive: "#28a745",
        neutral: "#ffc107",
        negative: "#dc3545",
        primary: "#667eea",
        secondary: "#764ba2",
        info: "#17a2b8",
        success: "#28a745",
        warning: "#ffc107",
        danger: "#dc3545",
    },

    /**
     * Default chart options
     */
    getDefaultOptions(type = "line") {
        const baseOptions = {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    position: "bottom",
                    labels: {
                        usePointStyle: true,
                        padding: 15,
                    },
                },
                tooltip: {
                    mode: "index",
                    intersect: false,
                    backgroundColor: "rgba(0, 0, 0, 0.8)",
                    padding: 12,
                    cornerRadius: 8,
                },
            },
        }

        if (type === "line") {
            baseOptions.scales = {
                x: {
                    grid: { display: false },
                },
                y: {
                    beginAtZero: true,
                    grid: { color: "rgba(0, 0, 0, 0.05)" },
                },
            }
        }

        return baseOptions
    },

    /**
     * Create gradient
     */
    createGradient(ctx, color1, color2) {
        const gradient = ctx.createLinearGradient(0, 0, 0, 300)
        gradient.addColorStop(0, color1)
        gradient.addColorStop(1, color2)
        return gradient
    },

    /**
     * Destroy chart safely
     */
    destroyChart(chart) {
        if (chart) {
            chart.destroy()
        }
    },
}

// Topic Chain Utilities
const TopicChainUtils = {
    /**
     * Get mood emoji based on mood string
     */
    getMoodEmoji(mood) {
        const emojis = {
            'позитивное': '😊',
            'позитивный': '😊',
            'отрицательное': '😞',
            'отрицательный': '😞',
            'нейтральное': '😐',
            'нейтральный': '😐',
            'воодушевляющее': '🚀',
            'энергичное': '⚡',
            'спокойное': '😌',
            'задумчивое': '🤔',
            'раздраженное': '😠',
            'веселое': '😄',
            'грустное': '😢',
            'удивленное': '😮'
        };
        return emojis[mood?.toLowerCase()] || '😐';
    },

    /**
     * Get mood CSS class
     */
    getMoodClass(mood) {
        const classes = {
            'позитивное': 'mood-positive',
            'позитивный': 'mood-positive',
            'отрицательное': 'mood-negative',
            'отрицательный': 'mood-negative',
            'нейтральное': 'mood-neutral',
            'нейтральный': 'mood-neutral',
            'воодушевляющее': 'mood-energetic',
            'энергичное': 'mood-energetic',
            'спокойное': 'mood-calm',
            'задумчивое': 'mood-thoughtful',
            'раздраженное': 'mood-irritated',
            'веселое': 'mood-happy',
            'грустное': 'mood-sad',
            'удивленное': 'mood-surprised'
        };
        return classes[mood?.toLowerCase()] || 'mood-neutral';
    },

    /**
     * Format mood for display
     */
    formatMood(mood) {
        if (!mood) return 'Неизвестно';

        const moodMap = {
            'позитивное и воодушевляющее': 'Позитивное и воодушевляющее',
            'позитивное': 'Позитивное',
            'отрицательное': 'Отрицательное',
            'нейтральное': 'Нейтральное',
            'воодушевляющее': 'Воодушевляющее',
            'энергичное': 'Энергичное',
            'спокойное': 'Спокойное',
            'задумчивое': 'Задумчивое',
            'раздраженное': 'Раздраженное',
            'веселое': 'Веселое',
            'грустное': 'Грустное',
            'удивленное': 'Удивленное'
        };

        return moodMap[mood?.toLowerCase()] || mood;
    },

    /**
     * Format sentiment for display
     */
    formatSentiment(score) {
        if (score > 0.3) return 'Позитив';
        if (score < -0.3) return 'Негатив';
        return 'Нейтрал';
    },

    /**
     * Build topic chain card HTML
     */
    /**
     * Build topic chain card HTML
     */
    buildChainCard(chain, source) {
        const sentimentClass = DashboardUtils.getSentimentClass(chain.avg_sentiment || 0)
        const platformIcon = DashboardUtils.getPlatformIcon(source?.platform)
        const sourceUrl = source ? DashboardUtils.buildSourceUrl(source) : "#"

        return `
            <div class="chain-item fade-in" id="chain-${chain.chain_id}">
                <div class="chain-header">
                    <div class="chain-title">
                        <i class="fas fa-link me-2"></i>
                        Цепочка #${chain.chain_id}
                    </div>
                    <div class="chain-meta">
                        <span><i class="fas fa-calendar me-1"></i> ${DashboardUtils.formatDate(chain.first_date)} - ${DashboardUtils.formatDate(chain.last_date)}</span>
                        <span><i class="fas fa-chart-bar me-1"></i> ${chain.analyses_count} анализов</span>
                    </div>
                </div>
                
                ${source ? `
                <div class="mb-3">
                    <a href="${sourceUrl}" target="_blank" class="source-link platform-${source.platform?.toLowerCase()}">
                        <i class="${platformIcon}"></i>
                        ${source.name}
                        <i class="fas fa-external-link-alt ms-1"></i>
                    </a>
                </div>
                ` : ""}
                
                <div class="chain-topics">
                    ${chain.topics ? chain.topics.map(topic => {
            // Извлекаем название темы из объекта или используем как строку
            const topicName = typeof topic === "object" && topic.topic ? topic.topic : String(topic)
            return `
                            <span class="topic-badge ${sentimentClass}">
                                ${topicName}
                            </span>
                        `
        }).join("") : ""}
                </div>
                
                <div class="mt-3">
                    <button class="btn btn-sm btn-outline-primary collapse-toggle" 
                            data-bs-toggle="collapse" 
                            data-bs-target="#evolution-${chain.chain_id}"
                            aria-expanded="false">
                        <i class="fas fa-chevron-right me-1"></i>
                        Показать эволюцию тем
                    </button>
                </div>
                
                <div class="collapse chain-evolution" id="evolution-${chain.chain_id}">
                    <div class="analysis-timeline" id="timeline-${chain.chain_id}">
                        <div class="text-center py-3">
                            <div class="spinner-border text-primary" role="status">
                                <span class="visually-hidden">Загрузка...</span>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        `
    },

    /**
     * Build evolution timeline HTML
     */
    buildEvolutionTimeline(evolution) {
        if (!evolution || evolution.length === 0) {
            return "<div class=\"empty-state\"><p>Нет данных об эволюции</p></div>"
        }

        return evolution.map(item => {
            const sentimentClass = DashboardUtils.getSentimentClass(item.sentiment_score)

            return `
                <div class="timeline-item">
                    <div class="timeline-date">
                        ${DashboardUtils.formatDateTime(item.analysis_date)}
                    </div>
                    <div class="timeline-content">
                        <div class="d-flex justify-content-between align-items-center mb-2">
                            <strong>Темы:</strong>
                            <span class="badge ${sentimentClass}">
                                ${DashboardUtils.getSentimentEmoji(item.sentiment_score)}
                                ${item.sentiment_score.toFixed(2)}
                            </span>
                        </div>
                        <div class="evolution-topics">
                            ${item.topics.map(topic => {
                // Извлекаем название темы из объекта или используем как строку
                const topicName = typeof topic === "object" && topic.topic ? topic.topic : String(topic)
                return `<span class="topic-badge">${topicName}</span>`
            }).join("")}
                        </div>
                        ${item.post_url ? `
                            <div class="mt-2">
                                <a href="${item.post_url}" target="_blank" class="btn btn-sm btn-outline-primary">
                                    <i class="fas fa-external-link-alt me-1"></i>
                                    Открыть пост
                                </a>
                            </div>
                        ` : ""}
                    </div>
                </div>
            `
        }).join("")
    },

    /**
     * Load evolution data when expanded
     */
    async loadEvolution(chainId) {
        try {
            const data = await DashboardUtils.fetchAPI(`/topic-chains/${chainId}/evolution`)
            const timelineEl = document.getElementById(`timeline-${chainId}`)

            if (timelineEl) {
                timelineEl.innerHTML = this.buildEvolutionTimeline(data)
            }
        } catch (error) {
            console.error("Error loading evolution:", error)
        }
    },
}

// Auto-refresh setup
let autoRefreshTimer = null

function setupAutoRefresh(callback, interval = DashboardConfig.AUTO_REFRESH_INTERVAL) {
    if (autoRefreshTimer) {
        clearInterval(autoRefreshTimer)
    }

    autoRefreshTimer = setInterval(callback, interval)
}

function stopAutoRefresh() {
    if (autoRefreshTimer) {
        clearInterval(autoRefreshTimer)
        autoRefreshTimer = null
    }
}

// Export for global use
window.DashboardConfig = DashboardConfig
window.DashboardUtils = DashboardUtils
window.ChartUtils = ChartUtils
window.TopicChainUtils = TopicChainUtils
window.setupAutoRefresh = setupAutoRefresh
window.stopAutoRefresh = stopAutoRefresh
