from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Dict, Optional
import pandas as pd
import numpy as np
from surprise import SVD, Dataset, Reader
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import linear_kernel
from pydantic import BaseModel
from passlib.context import CryptContext
import re # Для проверки английских букв
import pymorphy3
import requests
import httpx
import asyncio
import os
from datetime import datetime, timedelta

from sentence_transformers import SentenceTransformer, util

# Добавляем импорты для работы с БД
from sqlalchemy import create_engine, Column, Integer, String, Text, Float
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy import func, text

import nltk
from nltk.corpus import stopwords
nltk.download('stopwords')
russian_stopwords = stopwords.words('russian')

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__ident="2b")

# Настройки весов для системы рекомендаций
REC_WEIGHTS = {
    "genres": 1,      # Насколько важен жанр
    "staff": 1,       # Насколько важны актеры/режиссеры
    "description": 1  # Насколько важно текстовое описание
}

# --- 1. НАСТРОЙКА БАЗЫ ДАННЫХ И МОДЕЛИ БД ---
DATABASE_URL = "postgresql://postgres:your_password_here@localhost:5432/cinema_db"

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# СНАЧАЛА ОПИСЫВАЕМ МОДЕЛЬ
class MovieDB(Base):
    __tablename__ = "movies"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, index=True)
    genres = Column(String)
    poster_url = Column(String, nullable=True)
    description = Column(Text)
    imdb_rating = Column(Float, default=0.0)
    local_rating = Column(Float, default=0.0)
    votes_count = Column(Float, default=0)

class RatingDB(Base):
    __tablename__ = "ratings"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, index=True)
    movie_id = Column(Integer)
    rating = Column(Float)

# Схема для валидации входящих данных
class RatingCreate(BaseModel):
    user_id: int
    movie_id: int
    rating: float

class UserDB(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    password = Column(String)

class UserAuth(BaseModel):
    username: str
    password: str

# Схемы для рекомендаций
class RecommendationResponse(BaseModel):
    id: int
    title: str
    poster_url: Optional[str] = None
    genres: Optional[str] = None
    imdb_rating: Optional[float] = None
    description: Optional[str] = None
    reason: Optional[str] = None
    source: str  # "kinopoisk", "qwen_ai", "my_algo"

class ExternalAIRequest(BaseModel):
    user_id: int
    anchor_movie_id: Optional[int] = None
    user_history: Optional[List[Dict]] = None

class StaffDB(Base):
    __tablename__ = "staff"
    id = Column(Integer, primary_key=True)  # staffId
    name_ru = Column(String, nullable=True)
    name_en = Column(String, nullable=True)
    poster_url = Column(String, nullable=True)
    # Здесь храним основную профессию, но это поле опционально, 
    # так как роли определяются в таблице связей.

class MovieStaffDB(Base):
    __tablename__ = "movie_staff"
    movie_id = Column(Integer, primary_key=True)
    staff_id = Column(Integer, primary_key=True)
    profession_key = Column(String, primary_key=True) # Добавляем в ключ, т.к. один человек может быть и актером, и режиссером в одном фильме
    description = Column(String, nullable=True) # Роль (для актеров)
    order = Column(Integer, default=999)

# СОЗДАЕМ ТАБЛИЦЫ
Base.metadata.create_all(bind=engine)

# ФУНКЦИЯ ЗАГРУЗКИ
def get_data_from_db():
    db = SessionLocal()
    # Оборачиваем строку запроса в text()
    sql_query = text("""
        SELECT 
            m.id, m.title, 
            m.genres,
            m.description,
            string_agg(
                CASE 
                    -- ПЕРВЫЕ 3 ЧЕЛОВЕКА (индексы 0, 1, 2) - 5-кратный вес
                    WHEN ms.order <= 3 THEN REPEAT(REPLACE(s.name_ru, ' ', '_') || ' ', 2)
                    
                    -- СЛЕДУЮЩИЕ 3 ЧЕЛОВЕКА (индексы 3, 4, 5) - 3-кратный вес
                    WHEN ms.order <= 6 THEN REPEAT(REPLACE(s.name_ru, ' ', '_') || ' ', 1)
                    
                    -- ВСЕ ОСТАЛЬНЫЕ (актеры 2-3 плана, режиссеры дальше в списке и т.д.) - 1 вес
                    ELSE REPLACE(s.name_ru, ' ', '_')
                END, 
                ' '
            ) as staff_names
        FROM movies m
        LEFT JOIN movie_staff ms ON m.id = ms.movie_id
        LEFT JOIN staff s ON ms.staff_id = s.id
        GROUP BY m.id
    """)
    
    try:
        result = db.execute(sql_query)
        # В SQLAlchemy 2.0 данные извлекаются немного иначе через .mappings()
        query = result.mappings().all()
        
        data = []
        for item in query:
            # Склеиваем имена: "Марк Уолберг" -> "Марк_Уолберг"
            raw_staff = item["staff_names"] if item["staff_names"] else ""
            # Магия: заменяем пробелы между именами на подчеркивания, 
            # но сохраняем пробелы между разными людьми
            # Предположим, имена приходят разделенные запятой или двойным пробелом
            # Если они разделены просто пробелом, используем логику из SQL ниже

            data.append({
                "id": item["id"],
                "title": item["title"],
                "genres": item["genres"],
                "description": item["description"],
                "staff": raw_staff
            })
        return pd.DataFrame(data)
    except Exception as e:
        print(f"Ошибка при чтении из БД: {e}")
        return pd.DataFrame() # Возвращаем пустой датафрейм в случае ошибки
    finally:
        db.close()

app = FastAPI(title="Movie RecSys API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # В продакшене тут будет адрес твоего сайта
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- 2. ЗАГРУЗКА ДАННЫХ И ML ИНИЦИАЛИЗАЦИЯ ---

# Загружаем фильмы из БД
movies_df = get_data_from_db()

# ОПРЕДЕЛЯЕМ ФУНКЦИЮ ОБУЧЕНИЯ SVD (Она должна быть выше вызова)
def train_svd_model():
    db = SessionLocal()
    # Берем все оценки из базы (которые ты будешь слать через POST /rate)
    query = db.query(RatingDB).all()
    db.close()
    
    if not query:
        print("База оценок пуста. SVD не обучен.")
        return None

    # Если оценки есть, обучаемся на реальных данных из базы
    df = pd.DataFrame([{"user_id": r.user_id, "movie_id": r.movie_id, "rating": r.rating} for r in query])
    
    reader = Reader(rating_scale=(1, 10))
    data = Dataset.load_from_df(df[['user_id', 'movie_id', 'rating']], reader)
    
    model = SVD()
    model.fit(data.build_full_trainset())
    print(f"SVD переобучен на {len(df)} оценках.")
    return model

# Вызываем обучение модели при старте сервера
svd_model = train_svd_model()

# Инициализируем морфологический анализатор
morph = pymorphy3.MorphAnalyzer()

custom_stop_words = russian_stopwords + [
    'фильм', 'кино', 'история', 'сюжет', 'который', 'свой', 'весь', 
    'это', 'год', 'жизнь', 'время', 'герой', 'режиссер', 'роль',
    # Новые слова-паразиты (абстрактные понятия)
    'цель', 'день', 'дело', 'случай', 'место', 'образ', 'вид', 'часть',
    'мир', 'человек', 'друг', 'женщина', 'мужчина', 'ребенок', 'семья',
    'путь', 'сторона', 'конец', 'начало', 'город', 'страна', 'дом',
    'имя', 'слово', 'глаз', 'рука', 'раз', 'работа', 'помощь'
]

# Функция очистки и лемматизации
def preprocess_text(text, keep_all=False):
    if not isinstance(text, str):
        return ""
    
    # Очистка от спецсимволов
    text = re.sub(r'[^а-яА-ЯёЁ\s_]', '', text)
    words = text.lower().split()
    
    res = []
    for word in words:
        p = morph.parse(word)[0]
        
        # Если это стоп-слово - пропускаем
        if p.normal_form in custom_stop_words:
            continue
            
        # ЛОГИКА ФИЛЬТРАЦИИ:
        if keep_all:
            # Для Жанров и Стаффа берем всё (там имена нужны)
            res.append(p.normal_form)
        else:
            # Для ОПИСАНИЯ берем ТОЛЬКО нарицательные существительные (NOUN)
            # Исключаем PROPN (Имена собственные: Чарли, Москва, Борат и т.д.)
            if p.tag.POS == 'NOUN': 
                res.append(p.normal_form)
            
    return " ".join(res)


# === ЗАГРУЗКА SEMANTIC MODEL ===
print("Загрузка нейросети (это займет время при первом запуске)...")
# Модель для 50+ языков, отлично понимает русский
semantic_model = SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2')


# Инициализация TF-IDF на расширенных данных
# Формируем расширенный текст для анализа с использованием весов из конфига
if not movies_df.empty:
    movies_df['description'] = movies_df['description'].fillna('')
    movies_df['staff'] = movies_df['staff'].fillna('')
    movies_df['genres'] = movies_df['genres'].fillna('')
    
    # Подготовка данных
    movies_df['genres_clean'] = movies_df['genres'].apply(lambda x: preprocess_text(x, keep_all=True))
    movies_df['staff_clean'] = movies_df['staff'].apply(lambda x: preprocess_text(x, keep_all=True))
    
    # Для объяснений (тегов) сохраняем очищенные существительные
    movies_df['desc_keywords'] = movies_df['description'].apply(lambda x: preprocess_text(x, keep_all=False))

    # 1. Векторы Жанров и Стаффа (TF-IDF эффективнее для категорий)
    tfidf_genres = TfidfVectorizer()
    matrix_genres = tfidf_genres.fit_transform(movies_df['genres_clean'])
    
    tfidf_staff = TfidfVectorizer()
    matrix_staff = tfidf_staff.fit_transform(movies_df['staff_clean'])
    

    # === НОВАЯ ВСТАВКА: TF-IDF ДЛЯ ОПИСАНИЯ ===
    # Используем desc_keywords (там у тебя уже лежат существительные)
    # min_df=2 уберет слишком редкие слова (опечатки)
    print("Генерация TF-IDF для описаний...")
    tfidf_desc = TfidfVectorizer(min_df=1, max_features=5000) 
    matrix_desc_tfidf = tfidf_desc.fit_transform(movies_df['desc_keywords'])
    # ==========================================


    # 2. Векторы Описаний (SEMANTIC SEARCH)
    print("Генерация эмбеддингов описаний...")
    # Берем сырой текст, нейросеть сама разберется с контекстом
    descriptions_list = movies_df['description'].tolist()
    matrix_desc_semantic = semantic_model.encode(descriptions_list, convert_to_tensor=True, show_progress_bar=True)
    
    indices = pd.Series(movies_df.index, index=movies_df['id']).drop_duplicates()
    print("Система готова! Используется гибридный поиск (TF-IDF + Semantic).")

# Модель для запроса из "Конструктора"
class CustomRecRequest(BaseModel):
    user_id: int
    base_movie_ids: List[int]  # Выбранные фильмы для примера
    weights: Dict[str, float]  # Веса: {"genres": 5, "staff": 3, "description": 1}
    manual_keywords: Optional[str] = "" # Ручные ключевые слова

# --- 4. ЭНДПОИНТЫ ---



@app.get("/")
def read_root():
    return {"status": "Database connected and API is running"}

@app.get("/recommendations/{user_id}")
async def get_recommendations(user_id: int):
    REC_COUNT = 20 
    db = SessionLocal()
    
    ratings = db.query(RatingDB).filter(RatingDB.user_id == user_id).all()
    watched_ids = {r.movie_id for r in ratings}

    user_ratings_map = {r.movie_id: r.rating for r in ratings}

    if not ratings:
        # Запрос: Берем фильмы, считаем средний бал и кол-во голосов на нашем сайте,
        # но сортируем в первую очередь по IMDb (так как там база оценок больше)
        results = db.query(
            MovieDB, 
            func.avg(RatingDB.rating).label("avg"), 
            func.count(RatingDB.id).label("cnt")
        ).outerjoin(RatingDB, MovieDB.id == RatingDB.movie_id)\
         .group_by(MovieDB.id)\
         .order_by(MovieDB.imdb_rating.desc(), func.count(RatingDB.id).desc())\
         .limit(REC_COUNT).all()

        final = []
        for movie, avg, cnt in results:
            final.append({
                "id": movie.id, 
                "title": movie.title, 
                "genres": movie.genres,
                "description": movie.description, 
                "poster_url": movie.poster_url,
                "average_rating": round(avg, 1) if avg else 0, 
                "votes": cnt, 
                "imdb_rating": movie.imdb_rating,
                "user_rating": user_ratings_map.get(movie.id, 0)
            })
        db.close()
        return final

    top_movies = [r.movie_id for r in ratings if r.rating >= 7][:5]
    bad_movies = [r.movie_id for r in ratings if r.rating <= 4][:5]
    
    total_scores = np.zeros(movies_df.shape[0])

    # Считаем сходство для "лайков"
    for m_id in top_movies:
        if m_id in indices:
            idx = indices[m_id]
            
            # TF-IDF Similarity
            sim_g = linear_kernel(matrix_genres[idx], matrix_genres).flatten()
            sim_s = linear_kernel(matrix_staff[idx], matrix_staff).flatten()
            
            # Semantic Similarity (возвращает Tensor, переводим в numpy)
            base_vec = matrix_desc_semantic[idx]
            sim_d = util.cos_sim(base_vec, matrix_desc_semantic).cpu().numpy().flatten()
            
            total_scores += (sim_g * REC_WEIGHTS["genres"] + 
                             sim_s * REC_WEIGHTS["staff"] + 
                             sim_d * REC_WEIGHTS["description"])

    # Вычитаем анти-интересы
    for m_id in bad_movies:
        if m_id in indices:
            idx = indices[m_id]
            sim_g = linear_kernel(matrix_genres[idx], matrix_genres).flatten()
            sim_d = util.cos_sim(matrix_desc_semantic[idx], matrix_desc_semantic).cpu().numpy().flatten()
            # У жанров и сюжета сильный штраф, стафф меньше штрафуем
            total_scores -= (sim_g * 1.5 + sim_d * 1.0)

    combined_indices = np.argsort(total_scores)[::-1]
    
    rec_movie_ids = []
    for idx in combined_indices:
        m_id = int(movies_df.iloc[idx]['id'])
        if m_id not in watched_ids:
            rec_movie_ids.append(m_id)
        if len(rec_movie_ids) >= REC_COUNT:
            break

    # 4. Добор через SVD (если список пуст или мал)
    if len(rec_movie_ids) < REC_COUNT and svd_model is not None:
        all_movie_ids = movies_df['id'].unique()
        candidate_ids = [int(m_id) for m_id in all_movie_ids if m_id not in watched_ids and m_id not in rec_movie_ids]
        preds = sorted([(m_id, svd_model.predict(user_id, m_id).est) for m_id in candidate_ids], key=lambda x: x[1], reverse=True)
        for x in preds:
            rec_movie_ids.append(x[0])
            if len(rec_movie_ids) >= REC_COUNT:
                break

    # 5. Итоговый результат (с IMDb и средним баллом)
    # Формирование ответа
    results = db.query(MovieDB, func.avg(RatingDB.rating).label("avg"), func.count(RatingDB.id).label("cnt"))\
        .outerjoin(RatingDB, MovieDB.id == RatingDB.movie_id)\
        .filter(MovieDB.id.in_(rec_movie_ids[:REC_COUNT]))\
        .group_by(MovieDB.id).all()

    final = []
    for movie, avg, cnt in results:
        final.append({
            "id": movie.id, "title": movie.title, "genres": movie.genres,
            "description": movie.description, "poster_url": movie.poster_url,
            "average_rating": round(avg, 1) if avg else 0, "votes": cnt, "imdb_rating": movie.imdb_rating, "user_rating": user_ratings_map.get(movie.id, 0)
        })
    final.sort(key=lambda x: rec_movie_ids.index(x["id"]))
    db.close()
    return final

       
@app.get("/movies/{movie_id}/similar")
async def get_similar_movies(movie_id: int):
    if movie_id not in indices:
        raise HTTPException(status_code=404, detail="Movie not found")
    idx = indices[movie_id]
    
    # Гибридный расчет
    sim_g = linear_kernel(matrix_genres[idx], matrix_genres).flatten()
    sim_s = linear_kernel(matrix_staff[idx], matrix_staff).flatten()
    sim_d = util.cos_sim(matrix_desc_semantic[idx], matrix_desc_semantic).cpu().numpy().flatten()
    
    combined = sim_g + sim_s + sim_d
    
    sim_scores = sorted(list(enumerate(combined)), key=lambda x: x[1], reverse=True)
    movie_indices = [i[0] for i in sim_scores[1:4]]
    return movies_df.iloc[movie_indices].to_dict(orient='records')

@app.post("/rate")
async def rate_movie(data: RatingCreate):
    db = SessionLocal()
    
    try:
        # Проверяем, существует ли фильм
        movie = db.query(MovieDB).filter(MovieDB.id == data.movie_id).first()
        if not movie:
            db.close()
            print("Фильм не найден")
            raise HTTPException(status_code=404, detail="Фильм не найден")

        new_rating_obj = RatingDB(user_id=data.user_id, movie_id=data.movie_id, rating=data.rating)
        db.merge(new_rating_obj)
        db.commit()

        all_ratings = db.query(RatingDB).filter(RatingDB.movie_id == data.movie_id).all()
        count = len(all_ratings)
        avg = sum([r.rating for r in all_ratings]) / count

        movie.local_rating = round(avg, 1)
        movie.votes_count = count
        db.commit()

        res_rating = movie.local_rating
        res_count = movie.votes_count

    finally:
        db.close()
    
    global svd_model # Указываем, что меняем глобальную переменную
    svd_model = train_svd_model()

    return {
        "status": "success", 
        "message": f"Оценка {data.rating} сохранена",
        "new_local_rating": res_rating,
        "total_votes": res_count
        }

@app.get("/search")
async def search_movies(title: str, user_id: int = 0): # 1. Добавили аргумент user_id
    db = SessionLocal()
    # Лемматизируем поисковый запрос для морфологического поиска
    lemmatized_title = preprocess_text(title, keep_all=True)
    title_tokens = lemmatized_title.split() if lemmatized_title else [title]
    
    # 2. Ищем фильмы - используем ILIKE с токенами для морфологического поиска
    from sqlalchemy import or_
    query = db.query(
        MovieDB,
        func.avg(RatingDB.rating).label("avg_rating"),
        func.count(RatingDB.id).label("votes_count")
    ).outerjoin(RatingDB, MovieDB.id == RatingDB.movie_id)
    
    # Если есть лемматизированные токены - ищем по ним
    if title_tokens and len(title_tokens) > 0 and title_tokens[0]:
        # Строим условие поиска: фильм должен содержать хотя бы один из токенов
        conditions = [MovieDB.title.ilike(f"%{token}%") for token in title_tokens]
        query = query.filter(or_(*conditions))
    else:
        # Fallback на обычный поиск
        query = query.filter(MovieDB.title.ilike(f"%{title}%"))
    
    results = query.group_by(MovieDB.id).all()
    # 3. Достаем оценки пользователя (если он вошел)
    user_ratings_map = {}
    if user_id > 0:
        u_ratings = db.query(RatingDB).filter(RatingDB.user_id == user_id).all()
        user_ratings_map = {r.movie_id: r.rating for r in u_ratings}
    
    db.close()
    
    movies_with_ratings = []
    for movie, avg_rating, votes_count in results:
        movies_with_ratings.append({
            "id": movie.id,
            "title": movie.title,
            "genres": movie.genres,
            "description": movie.description,
            "poster_url": movie.poster_url,
            "average_rating": round(avg_rating, 1) if avg_rating else 0,
            "votes": votes_count,
            "imdb_rating": movie.imdb_rating,
            # 4. Вставляем оценку пользователя
            "user_rating": user_ratings_map.get(movie.id, 0) 
        })
        
    return movies_with_ratings

@app.post("/register")
async def register(data: UserAuth):
    # Валидация логина: длина 3-20, только a-zA-Z0-9_-
    if not re.match(r"^[a-zA-Z0-9_-]+$", data.username):
        raise HTTPException(status_code=400, detail="Логин может содержать только английские буквы, цифры, - и _")
    if len(data.username) < 3 or len(data.username) > 20:
        raise HTTPException(status_code=400, detail="Длина логина должна быть от 3 до 20 символов")
    
    # Валидация пароля: длина 6-50, только a-zA-Z0-9_-
    if not re.match(r"^[a-zA-Z0-9_-]+$", data.password):
        raise HTTPException(status_code=400, detail="Пароль может содержать только английские буквы, цифры, - и _")
    if len(data.password) < 6 or len(data.password) > 50:
        raise HTTPException(status_code=400, detail="Длина пароля должна быть от 6 до 50 символов")
    
    db = SessionLocal()

    # Проверка на уникальность
    exists = db.query(UserDB).filter(UserDB.username == data.username).first()
    if exists:
        db.close()
        raise HTTPException(status_code=400, detail="Этот логин уже занят")
    
    # Хеширование пароля
    hashed_password = pwd_context.hash(data.password)

    new_user = UserDB(username=data.username, password=hashed_password)
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    db.close()
    return {"id": new_user.id, "username": new_user.username}

@app.post("/login")
async def login(data: UserAuth):
    # Валидация логина
    if not re.match(r"^[a-zA-Z0-9_-]+$", data.username):
        raise HTTPException(status_code=400, detail="Логин может содержать только английские буквы, цифры, - и _")
    
    # Валидация пароля
    if not re.match(r"^[a-zA-Z0-9_-]+$", data.password):
        raise HTTPException(status_code=400, detail="Пароль может содержать только английские буквы, цифры, - и _")
    
    db = SessionLocal()
    user = db.query(UserDB).filter(UserDB.username == data.username).first()
    db.close()


    if not user or not pwd_context.verify(data.password, user.password):
        raise HTTPException(status_code=401, detail="Неверное имя или пароль")
    return {"id": user.id, "username": user.username}

@app.get("/movie/{movie_id}/staff")
async def get_movie_staff(movie_id: int):
    with SessionLocal() as db:
    
        try:
            # 1. Проверяем, есть ли данные в нашей базе
            # Присоединяем MovieStaffDB, чтобы достать профессию и описание роли
            existing_staff = db.query(StaffDB, MovieStaffDB).join(
                MovieStaffDB, StaffDB.id == MovieStaffDB.staff_id
            ).filter(MovieStaffDB.movie_id == movie_id).all()

            if existing_staff:
                results = []
                for staff_obj, rel_obj in existing_staff:
                    results.append({
                        "staffId": staff_obj.id,
                        "nameRu": staff_obj.name_ru,
                        "nameEn": staff_obj.name_en,
                        "posterUrl": staff_obj.poster_url,
                        "professionKey": rel_obj.profession_key, # Берем из связи
                        "description": rel_obj.description      # Берем из связи
                    })
                return results

            # 2. Если в базе нет, идем в API
            api_key = "5e8a9a31-4794-4e1f-9746-45f7c8345199"
            url = f"https://kinopoiskapiunofficial.tech/api/v1/staff?filmId={movie_id}"
            headers = {"X-API-KEY": api_key}
            
            response = requests.get(url, headers=headers, timeout=5)
            if response.status_code != 200:
                raise HTTPException(status_code=response.status_code, detail="Ошибка API Кинопоиска")
                
            api_data = response.json()
            
            # Фильтруем данные
            actors = [s for s in api_data if s['professionKey'] == 'ACTOR'][:10]
            directors = [s for s in api_data if s['professionKey'] == 'DIRECTOR']
            writers = [s for s in api_data if s['professionKey'] == 'WRITER'][:2]
            
            selected_staff = actors + directors + writers

            # 1. Сначала сохраняем УНИКАЛЬНЫХ людей в таблицу staff
            unique_persons = {s['staffId']: s for s in selected_staff}

            for s_id, s_data in unique_persons.items():
                new_person = StaffDB(
                    id=s_id,
                    name_ru=s_data.get('nameRu'),
                    name_en=s_data.get('nameEn'),
                    poster_url=s_data.get('posterUrl')
                )
                db.merge(new_person) # Теперь Кэмерон добавится только 1 раз

            # Сбрасываем изменения в базу, чтобы записи о людях точно существовали перед созданием связей
            db.flush() 

            # 2. Теперь сохраняем все их роли (связи) в movie_staff
            for idx, s in enumerate(selected_staff): # Добавили idx
                new_relation = MovieStaffDB(
                    movie_id=movie_id,
                    staff_id=s['staffId'],
                    profession_key=s['professionKey'],
                    description=s.get('description'),
                    order=idx  # <--- КРИТИЧЕСКИ ВАЖНО: сохраняем порядковый номер
                )
                db.merge(new_relation)
            
            db.commit()
            
            # Возвращаем именно тот список, который сформировали
            return selected_staff

        except Exception as e:
            db.rollback() # Откатываем изменения, если была ошибка
            print(f"ERROR: {e}")
            raise HTTPException(status_code=500, detail=str(e))

@app.post("/recommendations/custom")
async def get_custom_recommendations(req: CustomRecRequest):
    db = SessionLocal()
    REC_COUNT = 10
    
    # ПРОВЕРКА: Если входные данные пусты (нет фильмов и нет ключевых слов)
    # Возвращаем ТОП популярных фильмов по IMDb рейтингу
    if not req.base_movie_ids and not req.manual_keywords:
        results = db.query(
            MovieDB,
            func.avg(RatingDB.rating).label("avg"),
            func.count(RatingDB.id).label("cnt")
        ).outerjoin(RatingDB, MovieDB.id == RatingDB.movie_id)\
         .group_by(MovieDB.id)\
         .order_by(MovieDB.imdb_rating.desc())\
         .limit(REC_COUNT).all()

        final = []
        for movie, avg, cnt in results:
            final.append({
                "id": movie.id,
                "title": movie.title,
                "genres": movie.genres,
                "description": movie.description,
                "poster_url": movie.poster_url,
                "average_rating": round(avg, 1) if avg else 0,
                "votes": cnt,
                "imdb_rating": movie.imdb_rating,
                "match_reason": "Популярное",
                "user_rating": 0
            })
        db.close()
        return final


    # Инициализируем массив нулей длиной в количество всех фильмов
    # Сюда будем накапливать баллы похожести
    # Инициализируем массив нулей (используем любую матрицу для размера)
    total_scores = np.zeros(movies_df.shape[0])
    
    w_genres = req.weights.get('genres', 1)
    w_staff = req.weights.get('staff', 1)
    w_desc = req.weights.get('description', 1)

    for m_id in req.base_movie_ids:
        if m_id in indices:
            idx = indices[m_id]
            
            # TF-IDF (Быстрое точное совпадение для категорий)
            sim_g = linear_kernel(matrix_genres[idx], matrix_genres).flatten()
            sim_s = linear_kernel(matrix_staff[idx], matrix_staff).flatten()
            
            # НОВИНКА: Если есть хоть одно совпадение по актерам, 
            # мы возводим результат в степень 0.5 (извлекаем корень), 
            # чтобы маленькие значения (например, 0.1) превратились в большие (0.31)
            #sim_s = np.sqrt(sim_s)

            # SEMANTIC (Глубокое понимание смысла)
            # А. Умный поиск (SBERT)
            base_vec = matrix_desc_semantic[idx]
            sim_d = util.cos_sim(base_vec, matrix_desc_semantic).cpu().numpy().flatten()
            
            # Б. Точный поиск по словам (TF-IDF) - ловит "биржу", "деньги", "акции"
            sim_tfidf = linear_kernel(matrix_desc_tfidf[idx], matrix_desc_tfidf).flatten()

            # В. Смешиваем (50/50 или 70/30)
            # TF-IDF более резкий (много нулей), BERT более плавный.
            # Эта комбинация вытащит фильмы с похожими СЛОВАМИ наверх.
            sim_desc_final = (sim_d * 0.5) + (sim_tfidf * 0.5)

            # Фильтр "Шум SBERT":
            # Если семантика меньше 0.4, считаем это случайным шумом и обнуляем
            # Это уберет "Эту дурацкую любовь" от "Волка", так как там мало общего смысла
            sim_desc_final[sim_desc_final < 0.1] = 0

            # Если жанров нет общих и вес жанра высок, штрафуем
            # (реализуем через понижение коэффициента)
            sim_total = (sim_g * w_genres) + (sim_s * w_staff) + (sim_desc_final * w_desc)
            
            # # Если жанровое сходство 0, уменьшаем итоговый балл на 20%
            # mask_no_genre = (sim_g == 0)
            # # ТЕКУЩИЙ КОД:
            # # sim_total[mask_no_genre & (sim_s > 0)] *= 0.95 
            # # sim_total[mask_no_genre & (sim_s == 0)] *= 0.8
            
            # # НОВЫЙ ВАРИАНТ (Более жесткий):
            # # Если есть общий актер, штрафуем умеренно (пусть Макконахи останется)
            # sim_total[mask_no_genre & (sim_s > 0)] *= 0.85 
            
            # # Если НЕТ ни жанров, ни актеров общих — убиваем рейтинг
            # # Это уберет "Эту дурацкую любовь" из рекомендаций к "Волку"
            # sim_total[mask_no_genre & (sim_s == 0)] *= 0.3
            
            # 4. ШТРАФЫ (Упрощаем)
            # Если жанры совсем не совпали, режем балл пополам
            mask_no_genre = (sim_g == 0)
            sim_total[mask_no_genre] *= 0.5

            total_scores += sim_total

    # 1. Если пользователь ввел ключевые слова (ищем по матрице описаний)
    # Ручные ключевые слова (через нейросеть!)
    if req.manual_keywords:
        # Нейросеть превращает "грустное кино про братьев" в вектор
        kw_vec = semantic_model.encode(req.manual_keywords, convert_to_tensor=True)
        kw_sim = util.cos_sim(kw_vec, matrix_desc_semantic).cpu().numpy().flatten()
        total_scores += (kw_sim * 2.0)

    # Фильтрация
    # watched_ids = {r.movie_id for r in db.query(RatingDB).filter(RatingDB.user_id == req.user_id).all()}
    
    # Сначала достаем все оценки пользователя для фильтрации и для отображения звезд
    user_ratings = db.query(RatingDB).filter(RatingDB.user_id == req.user_id).all()
    watched_ids = {r.movie_id for r in user_ratings}
    user_ratings_map = {r.movie_id: r.rating for r in user_ratings} # Карта оценок
    ordered_indices = np.argsort(total_scores)[::-1]
    rec_ids = []
    
    for idx in ordered_indices:
        m_id = int(movies_df.iloc[idx]['id'])
        if m_id not in watched_ids and m_id not in req.base_movie_ids:
            rec_ids.append(m_id)
        if len(rec_ids) >= REC_COUNT:
            break

    # 5. Сбор данных и генерация объяснений
    sql = text("""
        SELECT m.id, m.title, m.genres, m.description, m.poster_url, m.imdb_rating,
        string_agg(DISTINCT REPLACE(s.name_ru, ' ', '_'), ' ') as staff_names,
        AVG(r.rating) as avg, COUNT(r.id) as cnt
        FROM movies m
        LEFT JOIN movie_staff ms ON m.id = ms.movie_id
        LEFT JOIN staff s ON ms.staff_id = s.id
        LEFT JOIN ratings r ON m.id = r.movie_id
        WHERE m.id IN :ids
        GROUP BY m.id
    """)

    results = db.execute(sql, {"ids": tuple(rec_ids)}).mappings().all()

    # --- ПОДГОТОВКА К ОБЪЯСНЕНИЮ ---
    source_genres = set()
    source_staff = set()
    # Собираем все важные существительные из базовых фильмов (это наши "темы")
    source_themes = set()

    for m_id in req.base_movie_ids:
        if m_id in indices:
            row = movies_df.iloc[indices[m_id]]
            if isinstance(row['genres'], str):
                source_genres.update([g.strip() for g in row['genres'].split(' ')])
            if isinstance(row['staff'], str):
                source_staff.update([s.strip() for s in row['staff'].split(' ')])
            # Для тем используем desc_keywords (очищенные существительные)
            if isinstance(row['desc_keywords'], str):
                source_themes.update(row['desc_keywords'].split())

    final_recs = []
    for row in results:
        match_reasons = []
        
        # 1. Сходство по Персонам
        movie_staff_str = row["staff_names"] if row["staff_names"] else ""
        movie_staff_set = set(movie_staff_str.split(' '))
        staff_intersect = list(source_staff.intersection(movie_staff_set))
        if staff_intersect:
            names = [n.replace('_', ' ') for n in staff_intersect if n and n != ""]
            if names:
                match_reasons.append(f"Персоны: {', '.join(names[:2])}")

        # 2. Сходство по Жанрам
        movie_genres_str = row["genres"] if row["genres"] else ""
        movie_genres_set = set(movie_genres_str.split(' '))
        genre_intersect = list(source_genres.intersection(movie_genres_set))
        if genre_intersect:
            match_reasons.append(f"Жанры: {', '.join(genre_intersect[:2])}")
            
        # 3. Сходство по Темам (самое интересное)
        # Сравниваем существительные из базовых фильмов с текущим
        current_movie_themes = set(preprocess_text(row["description"], keep_all=False).split())
        theme_intersect = list(source_themes.intersection(current_movie_themes))
        
        # Если нашли общие темы, которые не являются стоп-словами
        if theme_intersect:
            # Берем максимум 3 общих слова для пояснения
            match_reasons.append(f"Темы: {', '.join(theme_intersect[:3])}")

        # Если совпадений нет вообще (редко, но бывает)
        reason_text = " | ".join(match_reasons) if match_reasons else "Схожая атмосфера"

        final_recs.append({
            "id": row["id"],
            "title": row["title"],
            "genres": row["genres"],
            "description": row["description"],
            "poster_url": row["poster_url"],
            "imdb_rating": row["imdb_rating"] or 0,
            "average_rating": round(row["avg"], 1) if row["avg"] else 0, # Тут было avg_rating
            "votes": int(row["cnt"]), # Тут было votes_count
            "match_reason": reason_text,
            "user_rating": user_ratings_map.get(row["id"], 0)
        })

    final_recs.sort(key=lambda x: rec_ids.index(x["id"]))
    db.close()
    return final_recs

# ==========================================
# НОВЫЕ ЭНДПОИНТЫ ДЛЯ ВНЕШНИХ ИСТОЧНИКОВ
# ==========================================

# Кэш для Кинопоиска: {filmId: {"data": [...], "expires": datetime}}
kinopoisk_cache = {}
KINOPOISK_CACHE_TTL = timedelta(minutes=10)
KINOPOISK_API_KEY = "5e8a9a31-4794-4e1f-9746-45f7c8345199"

def get_top_rated_movies(user_id: int, limit: int = 2):
    """Получает ТОП-N фильмов пользователя по его оценкам"""
    db = SessionLocal()
    try:
        top_ratings = db.query(RatingDB).filter(
            RatingDB.user_id == user_id,
            RatingDB.rating >= 7
        ).order_by(RatingDB.rating.desc()).limit(limit).all()
        
        return [r.movie_id for r in top_ratings]
    finally:
        db.close()

async def fetch_kinopoisk_similars(client: httpx.AsyncClient, film_id: int):
    """Делает запрос к API Кинопоиска для получения похожих фильмов"""
    try:
        url = f"https://kinopoiskapiunofficial.tech/api/v2.2/films/{film_id}/similars"
        headers = {"X-API-KEY": KINOPOISK_API_KEY}
        response = await client.get(url, headers=headers)
        
        print(f"DEBUG KP: Запрос к filmId={film_id}, статус: {response.status_code}")
        
        if response.status_code != 200:
            print(f"DEBUG KP: Ошибка API для filmId={film_id}: {response.text[:200]}")
            return []
        
        api_data = response.json()
        films = api_data.get("items", [])
        
        print(f"DEBUG KP: Получено {len(films)} похожих фильмов для filmId={film_id}")
        
        # Получаем название якорного фильма для reason
        db = SessionLocal()
        anchor_movie = db.query(MovieDB).filter(MovieDB.id == film_id).first()
        anchor_title = anchor_movie.title if anchor_movie else None
        db.close()
        
        results = []
        for film in films:
            reason = f"Похож на «{anchor_title}»" if anchor_title else "Популярный фильм"
            
            # Проверяем наличие фильма в БД и добавляем если нет
            film_id_kp = film.get("filmId")
            db_add = SessionLocal()
            existing_movie = db_add.query(MovieDB).filter(MovieDB.id == film_id_kp).first()
            
            if not existing_movie and film_id_kp:
                # Добавляем новый фильм в БД
                new_movie = MovieDB(
                    id=film_id_kp,
                    title=film.get("nameRu") or film.get("titleRu") or "Unknown",
                    genres="",
                    description="",
                    poster_url=film.get("posterUrlPreview") or film.get("posterUrl"),
                    imdb_rating=film.get("rating") or 0.0,
                    local_rating=0.0,
                    votes_count=0
                )
                db_add.add(new_movie)
                db_add.commit()
                print(f"DEBUG KP: Добавлен фильм в БД: {new_movie.title} (ID: {film_id_kp})")
            
            db_add.close()
            
            results.append({
                "id": film_id_kp,
                "title": film.get("nameRu") or film.get("titleRu"),
                "poster_url": film.get("posterUrlPreview") or film.get("posterUrl"),
                "genres": None,
                "imdb_rating": film.get("rating"),
                "description": None,
                "reason": reason,
                "source": "kinopoisk",
                "_anchor_id": film_id
            })
        
        return results
    except Exception as e:
        print(f"DEBUG KP: Исключение при запросе filmId={film_id}: {str(e)}")
        return []

@app.get("/api/recommendations/kinopoisk")
async def get_kinopoisk_recommendations(user_id: int, anchor_movie_id: Optional[int] = None):
    """
    Получение рекомендаций от Кинопоиска с поддержкой мульти-якоря.
    Если передан anchor_movie_id — используется ТОЛЬКО он.
    Если нет — берём ТОП-2 по оценкам пользователя.
    """
    # 1. Собираем якорные фильмы
    anchor_ids = set()
    
    # ВАЖНО: Если пользователь явно выбрал фильм (anchor_movie_id), используем ТОЛЬКО его
    if anchor_movie_id:
        anchor_ids.add(anchor_movie_id)
        print(f"DEBUG KP: Используется пользовательский якорь: {anchor_movie_id}")
    else:
        # Если якорь не указан, берём ТОП из оценок пользователя
        top_movies = get_top_rated_movies(user_id, limit=2)
        for m_id in top_movies:
            anchor_ids.add(m_id)
            print(f"DEBUG KP: Добавлен якорь из ТОП оценок: {m_id}")
    
    if not anchor_ids:
        print("DEBUG KP: Нет якорных фильмов, берем популярные")
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                url = "https://kinopoiskapiunofficial.tech/api/v2.2/films/collections/type/top_popular_movies"
                headers = {"X-API-KEY": KINOPOISK_API_KEY}
                response = await client.get(url, headers=headers)
                
                if response.status_code == 200:
                    data = response.json()
                    items = data.get("items", [])[:10]
                    
                    results = []
                    for film in items:
                        results.append({
                            "id": film.get("filmId"),
                            "title": film.get("nameRu"),
                            "poster_url": film.get("posterUrlPreview"),
                            "genres": ", ".join([g["genre"] for g in film.get("genres", [])]) if isinstance(film.get("genres"), list) else None,
                            "imdb_rating": film.get("ratingImdb"),
                            "description": None,
                            "reason": "Популярный фильм",
                            "source": "kinopoisk"
                        })
                    return results
        except Exception as e:
            print(f"DEBUG KP: Ошибка при получении популярных фильмов: {e}")
        
        raise HTTPException(status_code=400, detail="Не удалось определить якорные фильмы")
    
    now = datetime.now()
    all_results = []
    anchors_to_fetch = []
    
    for a_id in anchor_ids:
        cache_key = f"kp_{a_id}"
        if cache_key in kinopoisk_cache:
            cache_entry = kinopoisk_cache[cache_key]
            if cache_entry["expires"] > now:
                print(f"DEBUG KP: Кэш хит для filmId={a_id}")
                all_results.extend(cache_entry["data"])
            else:
                anchors_to_fetch.append(a_id)
        else:
            anchors_to_fetch.append(a_id)
    
    if anchors_to_fetch:
        print(f"DEBUG KP: Делаем запросы для якорей: {anchors_to_fetch}")
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                tasks = [fetch_kinopoisk_similars(client, fid) for fid in anchors_to_fetch]
                fetched_results = await asyncio.gather(*tasks)
                
                for results in fetched_results:
                    all_results.extend(results)
                
                for i, a_id in enumerate(anchors_to_fetch):
                    cache_key = f"kp_{a_id}"
                    kinopoisk_cache[cache_key] = {
                        "data": fetched_results[i],
                        "expires": now + KINOPOISK_CACHE_TTL
                    }
        except Exception as e:
            print(f"DEBUG KP: Ошибка при параллельных запросах: {e}")
            raise HTTPException(status_code=503, detail=f"Ошибка соединения с Кинопоиском: {str(e)}")
    
    seen = {}
    for item in all_results:
        film_id = item.get("id")
        if film_id is None:
            continue
        
        if film_id not in seen:
            seen[film_id] = {
                "item": item,
                "count": 1,
                "anchors": {item.get("_anchor_id")}
            }
        else:
            seen[film_id]["count"] += 1
            seen[film_id]["anchors"].add(item.get("_anchor_id"))
    
    unique_results = []
    for film_id, data in seen.items():
        item = data["item"].copy()
        item.pop("_anchor_id", None)
        
        # Если reason ещё не установлен (или это мульти-якорь), устанавливаем корректный
        if data["count"] >= 2:
            # Получаем названия якорных фильмов для красивого reason
            db_reason = SessionLocal()
            anchor_titles = []
            for anchor_id in data["anchors"]:
                anchor_movie = db_reason.query(MovieDB).filter(MovieDB.id == anchor_id).first()
                if anchor_movie:
                    anchor_titles.append(anchor_movie.title)
            db_reason.close()
            
            if anchor_titles:
                item["reason"] = f"Похож на: {', '.join([f'«{t}»' for t in anchor_titles])}"
            else:
                item["reason"] = "Рекомендуется несколькими источниками"
        
        unique_results.append(item)
    
    unique_results.sort(key=lambda x: (-sum(1 for r in all_results if r.get("id") == x["id"]), x.get("title", "")))
    
    final_results = unique_results[:12]
    
    print(f"DEBUG KP: Возвращаем {len(final_results)} уникальных фильмов из {len(all_results)} полученных")
    
    return final_results


# DeepSeek API конфигурация
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "sk-13008908463341d99e453521659e99bd")
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-chat"

@app.post("/api/recommendations/external-ai")
async def get_external_ai_recommendations(req: ExternalAIRequest):
    """
    Получение рекомендаций от AI (DeepSeek).
    """
    db = SessionLocal()
    
    # 1. Собираем данные о пользователе
    user_ratings = db.query(RatingDB).filter(RatingDB.user_id == req.user_id).all()
    
    # Топ фильмы (оценка >= 7)
    top_movies = [r.movie_id for r in user_ratings if r.rating >= 7][:5]
    # Низкие оценки (оценка <= 4)
    low_movies = [r.movie_id for r in user_ratings if r.rating <= 4][:5]
    
    # Собираем жанры из топ фильмов
    top_genres = set()
    for m_id in top_movies:
        movie = db.query(MovieDB).filter(MovieDB.id == m_id).first()
        if movie and movie.genres:
            top_genres.update(movie.genres.split())
    
    db.close()
    
    # 2. Определяем anchor_movie_id если не указан
    anchor_movie_id = req.anchor_movie_id
    if not anchor_movie_id and top_movies:
        anchor_movie_id = top_movies[0]
    
    # Получаем информацию о якорном фильме для контекста
    anchor_context = ""
    anchor_title = ""
    if anchor_movie_id:
        db2 = SessionLocal()
        anchor_movie = db2.query(MovieDB).filter(MovieDB.id == anchor_movie_id).first()
        if anchor_movie:
            anchor_title = anchor_movie.title
            anchor_context = f"Особенно похож на: {anchor_movie.title}."
        db2.close()
    
    # 3. Формируем промпт
    prompt = f"""Порекомендуй 10 фильмов для пользователя.
Интересы (жанры): {', '.join(top_genres) if top_genres else 'не указаны'}.
Любимые фильмы (ID): {top_movies}.
Избегай (низкие оценки, ID): {low_movies}.
{anchor_context}
Верни ТОЛЬКО JSON массив в формате:
[{{"id": 123, "title": "Название", "year": 2020, "imdb_rating": 7.5, "genres": "боевик, драма", "description": "Краткое описание", "reason": "Почему рекомендуется"}}]
Если не знаешь точный ID, используй уникальные отрицательные числа (например -1, -2...)."""

    # 4. Запрос к DeepSeek API с fallback на популярные фильмы
    try:
        print(f"DEBUG DeepSeek: Отправка запроса к {DEEPSEEK_BASE_URL}")
        print(f"DEBUG DeepSeek: Промпт: {prompt[:500]}...")
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}"}
            payload = {
                "model": DEEPSEEK_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.7
            }
            response = await client.post(f"{DEEPSEEK_BASE_URL}/v1/chat/completions", json=payload, headers=headers)
            response.raise_for_status()
            ai_response = response.json()["choices"][0]["message"]["content"]
            
            print(f"DEBUG DeepSeek: Ответ API: {ai_response[:500]}...")
            
            # Парсим JSON ответ
            import json
            try:
                # Пытаемся найти JSON в ответе (если там есть лишние символы)
                start_idx = ai_response.find("[")
                end_idx = ai_response.rfind("]") + 1
                if start_idx >= 0 and end_idx > start_idx:
                    json_str = ai_response[start_idx:end_idx]
                    recommendations = json.loads(json_str)
                else:
                    recommendations = json.loads(ai_response)
                
                # Добавляем poster_url и source из БД
                db3 = SessionLocal()
                final_recs = []
                for rec in recommendations:
                    movie = db3.query(MovieDB).filter(MovieDB.id == rec.get("id")).first()
                    final_recs.append({
                        "id": rec.get("id"),
                        "title": rec.get("title", "Неизвестно"),
                        "poster_url": movie.poster_url if movie else None,
                        "genres": rec.get("genres"),
                        "imdb_rating": rec.get("imdb_rating"),
                        "description": rec.get("description"),
                        "reason": rec.get("reason", f"AI рекомендует: {anchor_context}"),
                        "source": "deepseek_ai"
                    })
                db3.close()
                
                return final_recs
                
            except json.JSONDecodeError as e:
                print(f"DEBUG DeepSeek: Ошибка парсинга JSON: {e}")
                raise HTTPException(status_code=500, detail=f"AI вернул некорректный JSON: {str(e)}")
        
    except httpx.HTTPStatusError as e:
        print(f"⚠️ DeepSeek API error: {e.response.status_code} - {e.response.text}")
        # Fallback на популярные фильмы при ошибке API
        return get_fallback_popular_movies()
    except Exception as e:
        print(f"⚠️ DeepSeek API error: {str(e)}")
        # Fallback на популярные фильмы при любой ошибке
        return get_fallback_popular_movies()


def get_fallback_popular_movies():
    """Возвращает популярные фильмы при недоступности DeepSeek API"""
    db = SessionLocal()
    try:
        results = db.query(MovieDB).order_by(MovieDB.imdb_rating.desc()).limit(10).all()
        final_recs = []
        for movie in results:
            final_recs.append({
                "id": movie.id,
                "title": movie.title,
                "poster_url": movie.poster_url,
                "genres": movie.genres,
                "imdb_rating": movie.imdb_rating,
                "description": movie.description,
                "reason": "🤖 AI временно недоступен (популярная подборка)",
                "source": "qwen_ai"  # Для совместимости с фронтендом
            })
        return final_recs
    finally:
        db.close()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
