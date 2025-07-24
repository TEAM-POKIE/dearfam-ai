from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import os
from dotenv import load_dotenv
from google import genai
from pydantic import BaseModel
from google.genai import types
from openai import OpenAI
import requests
from .s3_util import upload_image_to_s3
import json
import re
import logging


class DiaryRequest(BaseModel):
    user_text: str

load_dotenv()

logging.basicConfig(level=logging.INFO)

app = FastAPI()

# 정적 파일 서빙 (diary.html)
app.mount("/static", StaticFiles(directory="app/static"), name="static")

@app.get("/", response_class=HTMLResponse)
async def root_page():
    # 간단한 입력 폼 제공
    return HTMLResponse("""
    <!DOCTYPE html>
    <html lang=\"ko\">
    <head>
      <meta charset=\"UTF-8\">
      <title>AI 그림일기 생성</title>
    </head>
    <body>
      <h2>AI 그림일기 생성기</h2>
      <form id='diaryForm' method='post' action='/generate-diary'>
        <label>일기 소재를 입력하세요:</label><br>
        <textarea name='user_text' id='user_text' rows='4' cols='50'></textarea><br>
        <button type='submit'>AI로 생성하기</button>
      </form>
      <script>
        document.getElementById('diaryForm').onsubmit = async function(e) {
          e.preventDefault();
          const userText = document.getElementById('user_text').value;
          const res = await fetch('/generate-diary', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({user_text: userText})
          });
          const data = await res.json();
          // 결과를 쿼리 파라미터로 /diary로 전달
          const params = new URLSearchParams({
            diary_text: data.diary_text,
            image_url: data.image_url
          });
          window.location.href = '/diary?' + params.toString();
        }
      </script>
    </body>
    </html>
    """)

@app.get("/diary", response_class=HTMLResponse)
async def diary_page(request: Request):
    # 쿼리 파라미터에서 결과 받기
    diary_text = request.query_params.get("diary_text", "")
    image_url = request.query_params.get("image_url", "")
    with open("app/static/diary.html", encoding="utf-8") as f:
        html = f.read()
    # JS에서 diary_text, image_url을 사용할 수 있도록 스크립트 추가
    inject = f"""
    <script>
      window.DIARY_TEXT = `{diary_text}`;
      window.IMAGE_URL = `{image_url}`;
    </script>
    """
    html = html.replace("</head>", inject + "</head>")
    return HTMLResponse(html)

@app.post("/generate-diary")
async def generate_diary(req: DiaryRequest):
    user_text = req.user_text
    # Gemini로 2~3줄 일기 생성
    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    # 텍스트 생성 (Gemini-pro)
    response = client.models.generate_content(
        model="gemini-2.5-pro",
        contents=f"""{user_text} 
        내용을 바탕으로 초등학생 그림일기를 작성해줘.
        
        예시처럼 **JSON만** 응답해줘. (맨 앞에 json, 설명, 코드블록, 마크다운, 줄바꿈 등 아무것도 붙이지 마!)
        {{
            "year": "2024",
            "month": "12", 
            "day": "25",
            "weekday": "수",
            "title": "제목 (15자 이내)",
            "content": "일기 내용 (100자 이내)"
        }}

        """
    )
    diary_text = response.text
    logging.info(f"diary_text : {diary_text}")

    try:
        cleaned = re.sub(r"^```json|```$", "", diary_text.strip()).strip()
        diary_dict = json.loads(cleaned)
        logging.info(f"diary_dict : {diary_dict}")
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"Gemini 응답 JSON 파싱 실패: {e}")

    client = OpenAI(api_key=os.getenv("CHAT_GPT_API_KEY"))

    response = client.images.generate(
        model="dall-e-3",
        prompt=f"""{user_text}의 내용에 맞는 2D 만화 그림체로 그림을 그려줘. 
        둥글둥글하고 따뜻한 색감, 동화적인 스타일로 표현해줘.
        사진의 비율은 4:3 으로 꽉 채워주고, 나머지는 비슷한 색으로 여백을 채우줘""",
        size="1792x1024",
        n=1
    )
    image_url = response.data[0].url    # 이미지 다운로드
    image_data = requests.get(image_url).content

    # S3 업로드
    s3_url = upload_image_to_s3(image_data, "diary", 1, ext="png") # TODO : 나중에 가족 번호 추가해서, 경로 구분해야함.

    logging.info(json.dumps({
        "diary_text": diary_dict,
        "image_url": s3_url
    }, ensure_ascii=False, indent=2))

    # 이제 s3_url을 프론트에 전달
    return JSONResponse({
        "diary_text": diary_dict,
        "image_url": s3_url
    })
