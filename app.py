import streamlit as st
import feedparser
import os
import urllib.parse
import asyncio
import sys
import streamlit.components.v1 as components
from datetime import datetime, timedelta
from playwright.async_api import async_playwright
from notion_client import Client
from openai import OpenAI
from dotenv import load_dotenv
import time
import threading

# Windows 환경에서 asyncio 정책 설정
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

load_dotenv()

# --- 모델 설정 ---
# GMS (GPT 5 - nano) 모델명으로 업데이트
TARGET_MODEL = "gpt-5-nano" 
# SSAFY GMS API 엔드포인트 유지
client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"), 
    base_url="https://gms.ssafy.io/gmsapi/api.openai.com/v1"
)

notion = Client(auth=os.getenv("NOTION_TOKEN"))
NOTION_PAGE_ID = os.getenv("NOTION_PAGE_ID")

# --- 핵심 기능 함수 ---

def check_news_intent(user_input, conversation_history):
    """사용자 입력이 기사 검색 요청인지 판단 (GPT 5 - nano 활용)"""
    messages = conversation_history + [
        {"role": "system", "content": """당신은 사용자 의도 분류 전문가입니다. 
사용자 입력이 뉴스/기사 검색을 요구하는지 판단하세요.
뉴스 검색 요청 예시: "최근 AI 뉴스 알려줘", "삼성전자 기사 검색", "오늘 야구 기사"
일반 대화 예시: "안녕", "너는 누구야", "날씨 어때"

응답은 반드시 'NEWS' 또는 'CHAT' 중 하나만 출력하세요."""},
        {"role": "user", "content": user_input}
    ]
    
    try:
        response = client.chat.completions.create(
            model=TARGET_MODEL,
            messages=messages,
            max_tokens=10,
            temperature=0
        )
        intent = response.choices[0].message.content.strip().upper()
        return "NEWS" in intent
    except Exception as e:
        st.error(f"의도 판단 오류: {e}")
        return False

async def crawl_and_summarize(entry):
    """크롤링과 요약을 한 번에 처리 (GPT 5 - nano 활용)"""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            await page.goto(entry.link, timeout=30000)
            content = await page.inner_text("body")
            content = content[:2500] # 성능이 향상된 모델을 고려하여 컨텍스트 확장
            
            response = client.chat.completions.create(
                model=TARGET_MODEL,
                messages=[
                    {"role": "system", "content": "뉴스 요약 전문가입니다. GPT 5의 분석력을 활용해 3줄 이내로 핵심만 요약하세요."},
                    {"role": "user", "content": f"제목: {entry.title}\n본문: {content}"}
                ]
            )
            return response.choices[0].message.content
        except Exception as e:
            return f"요약 실패: {str(e)}"
        finally:
            await browser.close()

def search_and_process_news(keyword):
    """기사 검색 및 처리"""
    encoded_keyword = urllib.parse.quote(keyword)
    rss_url = f"https://news.google.com/rss/search?q={encoded_keyword}&hl=ko&gl=KR&ceid=KR:ko"
    feed = feedparser.parse(rss_url)
    
    if not feed.entries:
        return None, "관련 기사를 찾을 수 없습니다."
    
    results = []
    for entry in feed.entries[:3]:
        # 비동기 크롤링 및 요약 실행
        summary = asyncio.run(crawl_and_summarize(entry))
        results.append({
            "title": entry.title,
            "link": entry.link,
            "summary": summary
        })
    
    response_text = f"🔍 **'{keyword}'** 관련 최신 뉴스를 GPT 5 - nano가 분석했습니다.\n\n"
    for i, article in enumerate(results, 1):
        response_text += f"📰 **기사 {i}: {article['title']}**\n"
        response_text += f"{article['summary']}\n"
        response_text += f"🔗 [원문 링크]({article['link']})\n\n"
    
    return results, response_text

def general_chat_response(user_input, conversation_history):
    """일반 챗봇 응답 생성 (GPT 5 - nano 활용)"""
    messages = conversation_history + [
        {"role": "user", "content": user_input}
    ]
    
    try:
        response = client.chat.completions.create(
            model=TARGET_MODEL,
            messages=messages,
            temperature=0.7
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"응답 생성 중 오류가 발생했습니다: {e}"

def save_to_notion(title, summary, link):
    """노션에 기사 저장"""
    try:
        notion.pages.create(
            parent={"page_id": NOTION_PAGE_ID},
            properties={"title": [{"text": {"content": title}}]},
            children=[
                {"object": "block", "type": "heading_2", "heading_2": {"rich_text": [{"text": {"content": "📌 GPT 5 뉴스 리포트"}}]}},
                {"object": "block", "type": "paragraph", "paragraph": {"rich_text": [{"text": {"content": summary}}]}},
                {"object": "block", "type": "paragraph", "paragraph": {"rich_text": [{"text": {"content": f"🔗 원문: {link}"}}]}}
            ]
        )
        return True
    except Exception as e:
        st.error(f"노션 저장 오류: {e}")
        return False

def auto_collect_news(keyword):
    """자동 뉴스 수집 함수"""
    try:
        news_results, _ = search_and_process_news(keyword)
        if news_results:
            collection = {
                "keyword": keyword,
                "results": news_results,
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            st.session_state.collected_news.append(collection)
            for article in news_results:
                save_to_notion(f"[GPT5 자동수집] {article['title']}", article['summary'], article['link'])
        return True
    except Exception as e:
        st.error(f"자동 수집 오류: {e}")
        return False

# --- UI 레이아웃 ---
st.set_page_config(page_title="GPT 5 뉴스 어시스턴트", layout="wide")

# 세션 상태 초기화
if "messages" not in st.session_state:
    st.session_state.messages = []
if "schedules" not in st.session_state:
    st.session_state.schedules = []
if "collected_news" not in st.session_state:
    st.session_state.collected_news = []
if "last_check_time" not in st.session_state:
    st.session_state.last_check_time = {}

# 사이드바 레이아웃 (기존 로직 유지)
with st.sidebar:
    st.header("⏰ GPT 5 자동 스케줄러")
    with st.expander("➕ 새 스케줄 추가", expanded=True):
        schedule_type = st.radio("주기", ["매일", "특정 요일"], horizontal=True)
        search_keyword = st.text_input("키워드", placeholder="예: AI, 인공지능")
        col1, col2 = st.columns(2)
        with col1: hour = st.number_input("시", 0, 23, 9)
        with col2: minute = st.number_input("분", 0, 59, 0)
        
        if st.button("스케줄 추가"):
            if search_keyword:
                new_schedule = {
                    "id": len(st.session_state.schedules),
                    "type": schedule_type,
                    "keyword": search_keyword,
                    "hour": hour, "minute": minute,
                    "active": True
                }
                st.session_state.schedules.append(new_schedule)
                st.rerun()

# 메인 화면
st.title("📰 GMS (GPT 5 - nano) 뉴스 어시스턴트")
st.caption("차세대 AI 엔진으로 실시간 뉴스 분석 및 Notion 아카이빙")

# 대화 시스템 및 UI 처리 (사용자 제공 로직과 동일)
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])
        if message["role"] == "assistant" and "news_results" in message:
            cols = st.columns(len(message["news_results"]))
            for i, article in enumerate(message["news_results"]):
                with cols[i]:
                    if st.button(f"📌 저장 ({i+1})", key=f"notion_{i}_{message.get('timestamp', i)}"):
                        save_to_notion(article['title'], article['summary'], article['link'])
                        st.toast("저장 완료!")

if prompt := st.chat_input("뉴스 검색어나 질문을 입력하세요..."):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)
    
    conversation_history = [{"role": m["role"], "content": m["content"]} for m in st.session_state.messages[:-1]]
    
    with st.spinner("GPT 5 - nano 분석 중..."):
        if check_news_intent(prompt, conversation_history):
            news_results, response_text = search_and_process_news(prompt)
            assistant_message = {"role": "assistant", "content": response_text, "timestamp": time.time()}
            if news_results: assistant_message["news_results"] = news_results
            st.session_state.messages.append(assistant_message)
        else:
            response_text = general_chat_response(prompt, conversation_history)
            st.session_state.messages.append({"role": "assistant", "content": response_text})
    st.rerun()

# 60초마다 페이지 리로드 (자동 수집 체크용)
components.html("<script>setTimeout(function(){window.parent.location.reload();}, 60000);</script>", height=0)