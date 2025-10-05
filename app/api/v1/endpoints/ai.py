from typing import Optional
from fastapi import APIRouter, Depends, BackgroundTasks
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.services.user.auth import get_authenticated_user
from app.models.user import User
from app.schemas.ai import (
    AIAnalysisRequest,
    AIAnalysisResultInDB,
    SentimentAnalysisResult,
    TopicAnalysisResult,
)
from app.crud import ai as ai_crud
from app.celery.tasks import analyze_sentiment_task, analyze_topics_task

router = APIRouter(prefix="/", tags=["ai"])


@router.post("analyze/sentiment", response_model=SentimentAnalysisResult)
def analyze_sentiment(
    *,
    db: Session = Depends(get_db),
    analysis_in: AIAnalysisRequest,
    current_user: User = Depends(get_authenticated_user),
    background_tasks: BackgroundTasks,
):
    """
    Analyze sentiment of text content
    """
    if analysis_in.background:
        # Run in background
        background_tasks.add_task(
            analyze_sentiment_task.delay,
            text=analysis_in.text,
            user_id=current_user.id,
            source_id=analysis_in.source_id,
            source_type=analysis_in.source_type,
        )
        return {"message": "Analysis started in background"}
    
    # Run synchronously
    return ai_crud.analyze_sentiment(
        db,
        text=analysis_in.text,
        user_id=current_user.id,
        source_id=analysis_in.source_id,
        source_type=analysis_in.source_type,
    )


@router.post("analyze/topics", response_model=TopicAnalysisResult)
def analyze_topics(
    *,
    db: Session = Depends(get_db),
    analysis_in: AIAnalysisRequest,
    current_user: User = Depends(get_authenticated_user),
    background_tasks: BackgroundTasks,
):
    """
    Analyze topics in text content
    """
    if analysis_in.background:
        # Run in background
        background_tasks.add_task(
            analyze_topics_task.delay,
            text=analysis_in.text,
            user_id=current_user.id,
            source_id=analysis_in.source_id,
            source_type=analysis_in.source_type,
        )
        return {"message": "Analysis started in background"}
    
    # Run synchronously
    return ai_crud.analyze_topics(
        db,
        text=analysis_in.text,
        user_id=current_user.id,
        source_id=analysis_in.source_id,
        source_type=analysis_in.source_type,
    )


@router.get("results/", response_model=list[AIAnalysisResultInDB])
def get_analysis_results(
    db: Session = Depends(get_db),
    skip: int = 0,
    limit: int = 100,
    analysis_type: Optional[str] = None,
    current_user: User = Depends(get_authenticated_user),
):
    """
    Retrieve AI analysis results for current user
    """
    return ai_crud.get_analysis_results(
        db,
        user_id=current_user.id,
        analysis_type=analysis_type,
        skip=skip,
        limit=limit,
    )
