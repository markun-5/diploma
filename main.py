from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from typing import List
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
    "staff": 5,       # Насколько важны актеры/режиссеры
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

class UserCreate(BaseModel):
    username: str

class UserAuth(BaseModel):
    username: str
    password: str

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

# СОЗДАЕМ ТАБЛИЦЫ
Base.metadata.create_all(bind=engine)

# ФУНКЦИЯ ЗАГРУЗКИ
def get_data_from_db():
    db = SessionLocal()
    # Оборачиваем строку запроса в text()
    sql_query = text("""
        SELECT 
            m.id, m.title, m.genres, m.description,
            string_agg(DISTINCT s.name_ru, ' ') as staff_names
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
            data.append({
                "id": item["id"],
                "title": item["title"],
                "genres": item["genres"],
                "description": item["description"],
                "staff": item["staff_names"] if item["staff_names"] else ""
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

# Функция очистки и лемматизации
def preprocess_text(text):
    if not isinstance(text, str):
        return ""
    # 1. Оставляем только буквы (убираем запятые, точки, цифры)
    text = re.sub(r'[^а-яА-ЯёЁ\s]', '', text)
    # 2. Переводим в нижний регистр
    words = text.lower().split()
    # 3. Лемматизация (приводим к начальной форме)
    res = []
    for word in words:
        p = morph.parse(word)[0]
        # Если слово не в стоп-листе, добавляем
        if p.normal_form not in russian_stopwords:
            res.append(p.normal_form)
    return " ".join(res)

# Инициализация TF-IDF на расширенных данных
# Формируем расширенный текст для анализа с использованием весов из конфига
if not movies_df.empty:
    movies_df['description'] = movies_df['description'].fillna('')
    movies_df['staff'] = movies_df['staff'].fillna('')
    movies_df['genres'] = movies_df['genres'].fillna('')
    
    # Собираем контент, умножая строки на веса
    raw_content = (
        (movies_df['genres'] + " ") * REC_WEIGHTS["genres"] + 
        (movies_df['staff'] + " ") * REC_WEIGHTS["staff"] + 
        movies_df['description'] * REC_WEIGHTS["description"]
    )
    
    print(f"Обработка текстов (Веса: Жанры={REC_WEIGHTS['genres']}, Стафф={REC_WEIGHTS['staff']})...")
    movies_df['content_soup'] = raw_content.apply(preprocess_text)
    
    tfidf = TfidfVectorizer(stop_words=None) 
    tfidf_matrix = tfidf.fit_transform(movies_df['content_soup'])
    cosine_sim = linear_kernel(tfidf_matrix, tfidf_matrix)
    indices = pd.Series(movies_df.index, index=movies_df['id']).drop_duplicates()
    print("Улучшенная модель рекомендаций готова!")



# --- 4. ЭНДПОИНТЫ ---



@app.get("/")
def read_root():
    return {"status": "Database connected and API is running"}

@app.get("/recommendations/{user_id}")
async def get_recommendations(user_id: int):
    REC_COUNT = 10 
    db = SessionLocal()
    
    # 1. Получаем все оценки пользователя
    ratings = db.query(RatingDB).filter(RatingDB.user_id == user_id).all()
    watched_ids = {r.movie_id for r in ratings}

    if not ratings:
        random_movies = db.query(MovieDB).order_by(func.random()).limit(REC_COUNT).all()
        db.close()
        return random_movies

    # Разделяем на "Любимые" (>= 7) и "Нелюбимые" (<= 4)
    top_movies = [r.movie_id for r in ratings if r.rating >= 7][:5]
    bad_movies = [r.movie_id for r in ratings if r.rating <= 4][:5]
    
    rec_movie_ids = []

    # --- РАСЧЕТ ВЕКТОРОВ ---
    
    # Вектор интересов (Положительный)
    combined_soup = ""
    for m_id in top_movies:
        if m_id in indices:
            combined_soup += movies_df.iloc[indices[m_id]]['content_soup'] + " "
    
    # Вектор анти-интересов (Отрицательный)
    anti_soup = ""
    for m_id in bad_movies:
        if m_id in indices:
            anti_soup += movies_df.iloc[indices[m_id]]['content_soup'] + " "

    if combined_soup:
        # Сходство с тем, что НРАВИТСЯ
        query_vec = tfidf.transform([combined_soup])
        pos_sim_scores = linear_kernel(query_vec, tfidf_matrix).flatten()
        
        # Сходство с тем, что НЕ НРАВИТСЯ
        if anti_soup:
            anti_vec = tfidf.transform([anti_soup])
            neg_sim_scores = linear_kernel(anti_vec, tfidf_matrix).flatten()
            
            # Итоговый скор: Плюс за похожесть на лайки, Минус за похожесть на дизлайки
            # Коэффициент 0.6 означает, что дизлайки имеют сильное, но не абсолютное влияние
            final_scores = pos_sim_scores - (neg_sim_scores * 0.6)
        else:
            final_scores = pos_sim_scores

        # Сортируем по итоговому скору
        combined_indices = np.argsort(final_scores)[::-1]
        
        added = 0
        for idx in combined_indices:
            m_id = int(movies_df.iloc[idx]['id'])
            # Пропускаем уже виденное
            if m_id not in watched_ids:
                rec_movie_ids.append(m_id)
                added += 1
            if added >= REC_COUNT:
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
    results = db.query(
        MovieDB, 
        func.avg(RatingDB.rating).label("avg_rating"),
        func.count(RatingDB.id).label("votes_count")
    ).outerjoin(RatingDB, MovieDB.id == RatingDB.movie_id)\
     .filter(MovieDB.id.in_(rec_movie_ids[:REC_COUNT]))\
     .group_by(MovieDB.id).all()

    recommendations_list = []
    for movie, avg_rating, votes_count in results:
        recommendations_list.append({
            "id": movie.id, "title": movie.title, "genres": movie.genres,
            "description": movie.description, "poster_url": movie.poster_url,
            "average_rating": round(avg_rating, 1) if avg_rating else 0,
            "votes": votes_count, "imdb_rating": movie.imdb_rating
        })

    recommendations_list.sort(key=lambda x: rec_movie_ids.index(x["id"]))
    db.close()
    return recommendations_list

       
@app.get("/movies/{movie_id}/similar")
async def get_similar_movies(movie_id: int):
    if movie_id not in indices:
        raise HTTPException(status_code=404, detail="Movie not found")
    idx = indices[movie_id]
    sim_scores = sorted(list(enumerate(cosine_sim[idx])), key=lambda x: x[1], reverse=True)
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

@app.post("/users")
async def create_user(user_data: UserCreate):
    db = SessionLocal()
    # Проверяем, нет ли уже такого имени
    exists = db.query(UserDB).filter(UserDB.username == user_data.username).first()
    if exists:
        db.close()
        raise HTTPException(status_code=400, detail="Пользователь уже существует")
    
    new_user = UserDB(username=user_data.username)
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    db.close()
    return new_user

@app.get("/search")
async def search_movies(title: str):
    db = SessionLocal()
    # 1. Добавляем movie.imdb_rating в запрос
    results = db.query(
        MovieDB, 
        func.avg(RatingDB.rating).label("avg_rating"),
        func.count(RatingDB.id).label("votes_count")
    ).outerjoin(RatingDB, MovieDB.id == RatingDB.movie_id)\
     .filter(MovieDB.title.ilike(f"%{title}%"))\
     .group_by(MovieDB.id).all()
    
    db.close()
    
    movies_with_ratings = []
    for movie, avg_rating, votes_count in results:
        m_dict = {
            "id": movie.id,
            "title": movie.title,
            "genres": movie.genres,
            "description": movie.description,
            "poster_url": movie.poster_url,
            "average_rating": round(avg_rating, 1) if avg_rating else 0,
            "votes": votes_count,
            # ВАЖНО: Добавь эту строку ниже!
            "imdb_rating": movie.imdb_rating # Берем напрямую из объекта movie
        }
        movies_with_ratings.append(m_dict)
        
    return movies_with_ratings

@app.post("/register")
async def register(data: UserAuth):
    db = SessionLocal()

    # Проверка на уникальность
    exists = db.query(UserDB).filter(UserDB.username == data.username).first()
    if exists:
        db.close()
        raise HTTPException(status_code=400, detail="Этот логин уже занят")
    
    # Только английский и цифры (валидация)
    if not re.match(r"^[a-zA-Z0-9_]+$", data.username):
        raise HTTPException(status_code=400, detail="Логин может содержать только английские буквы и цифры")

    if len(data.password) < 6:
        raise HTTPException(status_code=400, detail="Пароль должен быть не короче 6 символов")
    
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
            actors = [s for s in api_data if s['professionKey'] == 'ACTOR'][:15]
            directors = [s for s in api_data if s['professionKey'] == 'DIRECTOR']
            writers = [s for s in api_data if s['professionKey'] == 'WRITER'][:3]
            
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
            for s in selected_staff:
                new_relation = MovieStaffDB(
                    movie_id=movie_id,
                    staff_id=s['staffId'],
                    profession_key=s['professionKey'],
                    description=s.get('description')
                )
                db.merge(new_relation)
            
            db.commit()
            
            # Возвращаем именно тот список, который сформировали
            return selected_staff

        except Exception as e:
            db.rollback() # Откатываем изменения, если была ошибка
            print(f"ERROR: {e}")
            raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)