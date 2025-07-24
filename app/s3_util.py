import os
import boto3
from dotenv import load_dotenv
import uuid

load_dotenv()

AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_S3_BUCKET = os.getenv("S3_BUCKET")
AWS_S3_REGION = os.getenv("S3_REGION")

if not all([AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_S3_BUCKET, AWS_S3_REGION]):
    raise ValueError("AWS 환경변수가 누락되었습니다.")

s3 = boto3.client(
    "s3",
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    region_name=AWS_S3_REGION
)

def upload_image_to_s3(image_bytes: bytes, directory: str, id: int, ext: str = "png") -> str:
    # 예: posts/1/uuid.png
    filename = f"{directory}/{id}/{uuid.uuid4().hex}.{ext}"
    try:
        s3.put_object(
            Bucket=AWS_S3_BUCKET,
            Key=filename,
            Body=image_bytes,
            ContentType=f"image/{ext}"
        )
    except Exception as e:
        raise RuntimeError(f"S3 업로드 실패: {e}")
    
    url = f"https://{AWS_S3_BUCKET}.s3.{AWS_S3_REGION}.amazonaws.com/{filename}"
    return url
