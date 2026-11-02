from fastapi import APIRouter, Depends, File, Request, UploadFile

from app.api.deps import current_user
from app.models.user import User
from app.schemas.community import UploadResult
from app.services import upload_service

router = APIRouter(prefix="/api", tags=["community"])


@router.post("/uploads", response_model=UploadResult, status_code=201)
async def upload_image(
    request: Request,
    file: UploadFile = File(..., description="jpg·png·webp, 5MB 이하"),
    _: User = Depends(current_user),
):
    """커뮤니티 댓글 사진 1장 올리기 (multipart, 칸 이름 file). 받은 image_url 을 댓글과 함께 보낸다."""
    data = await file.read(upload_service.MAX_BYTES + 1)
    name = upload_service.save_image(data)
    return UploadResult(image_url=upload_service.image_url(str(request.base_url), name))
