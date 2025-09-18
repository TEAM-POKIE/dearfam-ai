import os
import boto3
import logging
from dotenv import load_dotenv
import uuid
import aiohttp
import asyncio

load_dotenv()

AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_S3_BUCKET = os.getenv("S3_BUCKET")
AWS_S3_REGION = os.getenv("S3_REGION")
CDN_DOMAIN = os.getenv("CDN_DOMAIN")  # CloudFront 또는 CDN 도메인

if not all([AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_S3_BUCKET, AWS_S3_REGION]):
    raise ValueError("AWS 환경변수가 누락되었습니다.")

s3 = boto3.client(
    "s3",
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    region_name=AWS_S3_REGION
)

def upload_image_to_s3(image_bytes: bytes, directory: str, ext: str = "png") -> str:
    # 예: posts/1/uuid.png
    filename = f"temp/{directory}/{uuid.uuid4().hex}.{ext}"
    
    # Content-Type 매핑
    content_type_map = {
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg"
    }
    
    content_type = content_type_map.get(ext.lower(), f"image/{ext}")
    
    try:
        # 모든 이미지는 private으로 업로드 (CDN을 통해서만 접근)
        s3.put_object(
            Bucket=AWS_S3_BUCKET,
            Key=filename,
            Body=image_bytes,
            ContentType=content_type
        )
    except Exception as e:
        raise RuntimeError(f"S3 업로드 실패: {e}")
    
    # CDN 도메인이 설정되어 있으면 CDN URL 반환
    if CDN_DOMAIN:
        url = f"https://{CDN_DOMAIN}/{filename}"
    else:
        # temp 디렉토리 이미지는 CDN이 필수 (ModelsLab API 접근용)
        if directory == "temp":
            raise RuntimeError("CDN_DOMAIN 환경변수가 설정되지 않았습니다. temp 이미지는 CDN을 통해서만 접근 가능합니다.")
        url = f"https://{AWS_S3_BUCKET}.s3.{AWS_S3_REGION}.amazonaws.com/{filename}"
    
    return url


def upload_video_to_s3(video_bytes: bytes, is_temp: bool = True) -> str:
    directory = "temp/videos" if is_temp else "videos"
    key = f"{directory}/{uuid.uuid4().hex}.mp4"

    try:
        s3.put_object(
            Bucket=AWS_S3_BUCKET,
            Key=key,
            Body=video_bytes,
            ContentType="video/mp4"
        )
    except Exception as e:
        raise RuntimeError(f"S3 비디오 업로드 실패: {e}")
    
    # 비디오는 항상 S3 직접 URL 반환 (스프링부트에서 CDN 변환 처리)
    url = f"https://{AWS_S3_BUCKET}.s3.{AWS_S3_REGION}.amazonaws.com/{key}"
    return url


async def download_and_upload_image_to_s3(image_url: str, is_temp: bool = True) -> str:
    """이미지 URL을 다운로드하여 S3에 업로드"""
    directory = "temp/diary" if is_temp else "diary"
    key = f"{directory}/{uuid.uuid4().hex}.png"
    
    try:
        logging.info(f"이미지 다운로드 시작: {image_url}")
        
        async with aiohttp.ClientSession() as session:
            async with session.get(image_url) as response:
                if response.status != 200:
                    raise Exception(f"이미지 다운로드 실패 ({response.status})")
                
                image_data = await response.read()
                logging.info(f"이미지 다운로드 완료: {len(image_data)} bytes")
                
                # S3에 업로드
                s3.put_object(
                    Bucket=AWS_S3_BUCKET,
                    Key=key,
                    Body=image_data,
                    ContentType="image/png"
                )
                
                url = f"https://{AWS_S3_BUCKET}.s3.{AWS_S3_REGION}.amazonaws.com/{key}"
                logging.info(f"이미지 S3 업로드 완료: {url}")
                return url
                
    except Exception as e:
        logging.error(f"이미지 다운로드/업로드 실패: {str(e)}")
        raise Exception(f"이미지 처리 실패: {str(e)}")


def delete_file_from_s3(file_url: str) -> bool:
    try:
        # CDN URL과 S3 직접 URL 모두 처리
        if CDN_DOMAIN and file_url.startswith(f"https://{CDN_DOMAIN}/"):
            key = file_url.replace(f"https://{CDN_DOMAIN}/", "")
        else:
            key = file_url.replace(f"https://{AWS_S3_BUCKET}.s3.{AWS_S3_REGION}.amazonaws.com/", "")
        
        s3.delete_object(Bucket=AWS_S3_BUCKET, Key=key)
        return True
    except Exception as e:
        logging.error(f"S3 파일 삭제 실패: {e}")
        return False

