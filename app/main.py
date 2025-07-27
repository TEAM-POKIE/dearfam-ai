from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from typing import List
import os
import logging
import json
import re
import asyncio
from dotenv import load_dotenv

from google import genai
from openai import AsyncOpenAI

# 환경 변수 로드
load_dotenv()

# 로깅 설정
logging.basicConfig(level=logging.INFO)

# FastAPI 앱 생성
app = FastAPI()

# Gemini 클라이언트
gemini_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

# OpenAI 비동기 클라이언트
openai_client = AsyncOpenAI(api_key=os.getenv("CHAT_GPT_API_KEY"))

class DiaryRequest(BaseModel):
    user_text: List[str] = Field(..., description="스프링부트에서 전달받는 문장 리스트")


async def generate_single_diary(text: str, text_index: int):
    try:
        # Gemini 일기 생성
        gemini_response = gemini_client.models.generate_content(
            model="gemini-2.5-pro",
            contents=f"""
            {text} 
            내용을 바탕으로 초등학생 그림일기를 작성해줘.

            예시처럼 **JSON만** 응답해줘. (맨 앞에 json, 설명, 코드블록, 마크다운, 줄바꿈 등 아무것도 붙이지 마!)
            {{
                "title": "제목 (15자 이내)",
                "content": "일기 내용 (100자 이내)"
            }}
            """
        )

        diary_text = gemini_response.text.strip()
        try:
            cleaned = re.sub(r"^```json|```$", "", diary_text).strip()
            diary_dict = json.loads(cleaned)
        except Exception as parse_error:
            raise ValueError(f"Gemini JSON 파싱 실패: {parse_error}")

        # DALL·E-3 이미지 비동기 생성
        image_response = await openai_client.images.generate(
            model="dall-e-3",
            prompt=f"""
            {text}의 내용에 맞는 2D 만화 그림체로 그림을 그려줘. 
            둥글둥글하고 따뜻한 색감, 동화적인 스타일로 표현해줘.
            전체 사진의 크기는 16:9기지만, 나타내는 사진의 비율은 4:3으로 꽉 채워주고, 
            나머지는 비슷한 색으로 여백을 채워줘.
            """,
            size="1792x1024",
            n=1
        )

        image_url = image_response.data[0].url

        return {
            "text_index": text_index,
            "title": diary_dict.get("title", ""),
            "content": diary_dict.get("content", ""),
            "image_url": image_url
        }

    except Exception as e:
        logging.error(f"[{text_index}] 처리 실패: {str(e)}")
        return {
            "text_index": text_index,
            "title": "처리 실패",
            "content": "처리 실패",
            "image_url": "처리 실패"
        }


@app.post("/generate-diary")
async def generate_diary(req: DiaryRequest):
    user_texts = req.user_text
    logging.info(f"총 {len(user_texts)}개의 텍스트 요청 수신")

    results = []
    
    # 3개씩 나누어서 처리
    batch_size = 3
    for batch_start in range(0, len(user_texts), batch_size):
        batch_end = min(batch_start + batch_size, len(user_texts))
        batch_texts = user_texts[batch_start:batch_end]
        
        logging.info(f"배치 {batch_start//batch_size + 1} 처리 시작: {batch_start}~{batch_end-1}")
        
        # 현재 배치를 병렬 처리
        batch_tasks = []
        for i, text in enumerate(batch_texts):
            text_index = batch_start + i
            task = generate_single_diary(text, text_index)
            batch_tasks.append(task)
        
        batch_results = await asyncio.gather(*batch_tasks)
        results.extend(batch_results)
        
        logging.info(f"배치 {batch_start//batch_size + 1} 완료")
        
        # 마지막 배치가 아니면 rate limit을 위한 대기
        if batch_end < len(user_texts):
            logging.info("다음 배치를 위해 12초 대기...")
            await asyncio.sleep(12)

    logging.info(f"총 {len(results)}개 처리 완료")

    return JSONResponse({
        "results": results
    })