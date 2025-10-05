from fastapi import HTTPException


class SCRFException(HTTPException):
    def __init__(self):
        super().__init__(
            status_code=403,
            detail={
                "error": "invalid_scrf_token",
                "message": "Security token is invalid or expired",
                "solution": "Please refresh the page and try again"
            }
        )
