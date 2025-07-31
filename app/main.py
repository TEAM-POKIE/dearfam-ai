from fastapi import FastAPI, File, UploadFile, Form
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
import logging
from dotenv import load_dotenv
from .ai_services import DiaryAIService, VideoAIService
from .s3_util import upload_image_to_s3
# 환경 변수 로드
load_dotenv()

# 로깅 설정
logging.basicConfig(level=logging.INFO)

# FastAPI 앱 생성
app = FastAPI()

# 일기 생성 API 요청 모델
class DiaryRequest(BaseModel):
    user_text: str = Field(..., description="일기 생성용 텍스트")


@app.post("/generate-diary")
async def generate_diary(req: DiaryRequest):
    """일기 생성 API"""
    user_text = req.user_text
    logging.info(f"일기 생성 요청: {user_text[:50]}...")

    result = await DiaryAIService.generate_diary(user_text)
    
    logging.info(f"일기 생성 완료")
    return JSONResponse(result)


@app.post("/animate-image")
async def animate_image(
    image: UploadFile = File(..., description="영상화할 이미지 파일"),
    prompt: str = Form(..., description="영상화 프롬프트")
):
    """사진 영상화 API"""
    try:
        # 이미지 파일 읽기
        image_data = await image.read()
        logging.info(f"영상화 요청: {image.filename}, 프롬프트: {prompt[:50]}...")

        # 파일 크기 검증 (예: 10MB 제한)
        if len(image_data) > 10 * 1024 * 1024:  # 10MB
            return JSONResponse({
                "status": "error",
                "message": "파일 크기가 너무 큽니다. 10MB 이하로 업로드해주세요."
            }, status_code=400)

        # 파일 형식 검증
        allowed_types = ["image/jpeg", "image/png", "image/jpg"]
        if image.content_type not in allowed_types:
            return JSONResponse({
                "status": "error", 
                "message": "지원하지 않는 파일 형식입니다. JPEG, PNG 파일만 업로드 가능합니다."
            }, status_code=400)

        # 이미지 방향 수정 (EXIF 정보 제거)
        from PIL import Image
        import io
        
        # PIL로 이미지 열기
        image = Image.open(io.BytesIO(image_data))
        
        # EXIF 정보 제거하고 올바른 방향으로 회전
        if hasattr(image, '_getexif') and image._getexif() is not None:
            exif = image._getexif()
            orientation = exif.get(274)  # EXIF orientation tag
            if orientation:
                # 방향에 따라 회전
                if orientation == 3:
                    image = image.rotate(180, expand=True)
                elif orientation == 6:
                    image = image.rotate(270, expand=True)
                elif orientation == 8:
                    image = image.rotate(90, expand=True)
        
        # 이미지를 PNG로 변환 (EXIF 정보 제거)
        output_buffer = io.BytesIO()
        image.save(output_buffer, format='PNG')
        corrected_image_data = output_buffer.getvalue()
        
        # 수정된 이미지를 S3에 임시 업로드
        image_url = upload_image_to_s3(corrected_image_data, "temp", "png")
        logging.info(f"이미지 방향 수정 후 임시 업로드 완료: {image_url}")

        # 영상화 처리 (비디오를 S3에 저장)
        result = await VideoAIService.animate_image(image_url, prompt)
        
        logging.info(f"영상화 완료: {result.get('status', 'unknown')}")
        return JSONResponse(result)

    except Exception as e:
        logging.error(f"영상화 처리 중 오류: {str(e)}")
        return JSONResponse({
            "status": "error",
            "message": f"영상화 처리 중 오류가 발생했습니다: {str(e)}"
        }, status_code=500)