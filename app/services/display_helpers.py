"""
Display helpers for rich analytics visualization in dashboard.
"""

from typing import Dict, Any, List, Optional


class RichDisplayHelper:
    """Helper for rich display of analytics data in dashboard."""
    
    # Sentiment emoji mapping
    SENTIMENT_EMOJI = {
        "Позитивный": "😊",
        "Положительный": "😊",
        "Позитивный настрой": "😊",
        "Негативный": "😞",
        "Отрицательный": "😞",
        "Негативный настрой": "😞",
        "Нейтральный": "😐",
        "Спокойный": "😐",
        "Смешанный": "🤔",
        "Противоречивый": "🤔",
        "Возбужденный": "🤩",
        "Радостный": "😄",
        "Грустный": "😔",
        "Сердитый": "😠",
        "Удивленный": "😲"
    }
    
    @classmethod
    def format_sentiment(cls, sentiment_label: str, sentiment_score: Optional[float] = None) -> str:
        """
        Format sentiment with emoji.
        
        Args:
            sentiment_label: Sentiment label from analysis
            sentiment_score: Optional sentiment score (0.0-1.0)
        
        Returns:
            Formatted string with emoji
        """
        emoji = cls.SENTIMENT_EMOJI.get(sentiment_label, "💭")
        
        if sentiment_score is not None:
            return f"{emoji} {sentiment_label} ({sentiment_score:.2f})"
        else:
            return f"{emoji} {sentiment_label}"
    
    @classmethod
    def format_keywords(cls, keywords: List[str], max_display: int = 10) -> List[Dict[str, str]]:
        """
        Format keywords for badge display.
        
        Args:
            keywords: List of keywords
            max_display: Maximum keywords to display
        
        Returns:
            List of dicts with keyword and style
        """
        formatted = []
        for keyword in keywords[:max_display]:
            formatted.append({
                "text": keyword,
                "class": "badge badge-primary"
            })
        
        if len(keywords) > max_display:
            formatted.append({
                "text": f"+{len(keywords) - max_display} еще",
                "class": "badge badge-secondary"
            })
        
        return formatted
    
    @classmethod
    def format_source_links(
        cls,
        analysis_data: Dict[str, Any],
        platform_name: str
    ) -> List[Dict[str, str]]:
        """
        Generate source links from analysis data.
        
        Args:
            analysis_data: Analysis summary_data
            platform_name: Platform name
        
        Returns:
            List of dicts with link info
        """
        links = []
        
        # Extract events if present
        multi_llm = analysis_data.get('multi_llm_analysis', {})
        text_analysis = multi_llm.get('text_analysis', {})
        events = text_analysis.get('events', [])
        
        # Get platform icon
        platform_icon = {
            "ВКонтакте": "fab fa-vk",
            "Telegram": "fab fa-telegram",
            "Twitter": "fab fa-twitter",
            "Instagram": "fab fa-instagram",
            "YouTube": "fab fa-youtube"
        }.get(platform_name, "fas fa-link")
        
        for event in events[:5]:  # Limit to 5 links
            if isinstance(event, dict):
                event_id = event.get('event_id')
                event_type = event.get('type', 'post')
                
                if event_id:
                    # Build link based on platform
                    if platform_name == "ВКонтакте":
                        url = f"https://vk.com/{event_id}"
                    else:
                        url = None
                    
                    if url:
                        links.append({
                            "url": url,
                            "text": f"{event_type.capitalize()}",
                            "icon": platform_icon
                        })
        
        return links
    
    @classmethod
    def format_trigger_reason(
        cls,
        trigger_type: Optional[str],
        trigger_metadata: Optional[Dict[str, Any]]
    ) -> Optional[Dict[str, str]]:
        """
        Format trigger activation reason.
        
        Args:
            trigger_type: Type of trigger
            trigger_metadata: Metadata about trigger activation
        
        Returns:
            Dict with trigger info or None
        """
        if not trigger_type or not trigger_metadata:
            return None
        
        reason_templates = {
            "KEYWORD_MATCH": "Найдено ключевое слово: {matched_keyword}",
            "SENTIMENT_THRESHOLD": "Тональность {direction} порога: {threshold}",
            "ACTIVITY_SPIKE": "Всплеск активности: +{spike_percent}%",
            "USER_MENTION": "Упоминание пользователя: {mentioned_user}"
        }
        
        template = reason_templates.get(trigger_type, "Сработал триггер: {trigger_type}")
        
        try:
            reason = template.format(
                trigger_type=trigger_type,
                **trigger_metadata
            )
        except KeyError:
            reason = f"Сработал триггер: {trigger_type}"
        
        return {
            "type": trigger_type,
            "reason": reason,
            "icon": "fas fa-bell",
            "class": "alert alert-info"
        }
    
    @classmethod
    def format_analysis_for_display(
        cls,
        analytics: 'AIAnalytics',
        display_config: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Format full analytics record for rich display.
        
        Args:
            analytics: AIAnalytics model instance
            display_config: Optional display configuration
        
        Returns:
            Dict with formatted display data
        """
        display_config = display_config or {}
        summary_data = analytics.summary_data or {}
        
        multi_llm = summary_data.get('multi_llm_analysis', {})
        text_analysis = multi_llm.get('text_analysis', {})
        source_metadata = summary_data.get('source_metadata', {})
        
        result = {
            "title": summary_data.get('analysis_title', 'Без названия'),
            "summary": summary_data.get('analysis_summary', ''),
            "date": analytics.analysis_date,
            "id": analytics.id
        }
        
        # Add sentiment with emoji
        if display_config.get('show_sentiment_emoji', True):
            sentiment_label = text_analysis.get('sentiment_label')
            sentiment_score = text_analysis.get('sentiment_score')
            
            if sentiment_label:
                result['sentiment'] = cls.format_sentiment(sentiment_label, sentiment_score)
        
        # Add keywords
        if display_config.get('show_keywords', True):
            keywords = text_analysis.get('keywords', [])
            if keywords:
                result['keywords'] = cls.format_keywords(keywords)
        
        # Add source links
        if display_config.get('show_source_links', True):
            platform_name = source_metadata.get('platform', 'Unknown')
            links = cls.format_source_links(summary_data, platform_name)
            if links:
                result['source_links'] = links
        
        # Add trigger reason
        if display_config.get('show_trigger_reason', True):
            trigger_type = summary_data.get('trigger_type')
            trigger_metadata = summary_data.get('trigger_metadata')
            
            trigger_info = cls.format_trigger_reason(trigger_type, trigger_metadata)
            if trigger_info:
                result['trigger_info'] = trigger_info
        
        # Add content statistics
        content_stats = summary_data.get('content_statistics', {})
        if content_stats:
            result['stats'] = {
                "posts": content_stats.get('total_posts', 0),
                "reactions": content_stats.get('total_reactions', 0),
                "comments": content_stats.get('total_comments', 0)
            }
        
        return result
