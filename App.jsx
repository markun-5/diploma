import React, { useState, useEffect } from 'react';
import axios from 'axios';
import { Search, Heart, Film, User } from 'lucide-react';

// Настраиваем адрес нашего бэкенда
const API_URL = "http://127.0.0.1:8000";

function App() {
  const [movies, setMovies] = useState([]);
  const [search, setSearch] = useState("");
  const [recommendations, setRecommendations] = useState([]);

  const [selectedStaff, setSelectedStaff] = useState({});

  const fetchStaff = async (movieId) => {
  if (selectedStaff[movieId]) return;

  try {
    const res = await axios.get(`${API_URL}/movie/${movieId}/staff`);
    
    // Группируем данные
    const staff = {
      actors: res.data.filter(s => s.professionKey === 'ACTOR'),
      directors: res.data.filter(s => s.professionKey === 'DIRECTOR'),
      writers: res.data.filter(s => s.professionKey === 'WRITER')
    };

    setSelectedStaff(prev => ({ ...prev, [movieId]: staff }));
  } catch (err) {
    console.error("Ошибка загрузки состава", err);
  }
};

  const [user, setUser] = useState(JSON.parse(localStorage.getItem('user')));
  const [authData, setAuthData] = useState({username: '', password: ''});
  const [isRegistering, setIsRegistering] = useState(false);
  
  const handleAuth = async (e) => {
    if (e) e.preventDefault();
    const endpoint = isRegistering ? '/register' : '/login';
    try {
      const res = await axios.post(`${API_URL}${endpoint}`, authData);
      localStorage.setItem('user', JSON.stringify(res.data));
      setUser(res.data);
    } catch (err) {
      alert(err.response?.data?.detail || "Ошибка доступа");
    }
  };

  const handleLogout = () => {
    localStorage.removeItem('user');
    setUser(null);
    setSearch("");
    setMovies([]);
    setRecommendations([]);
  }

  // 1. Функция поиска фильмов
  const handleSearch = async (e) => {
    e.preventDefault();
    try {
      const res = await axios.get(`${API_URL}/search?title=${search}`);
      setMovies(res.data);
    } catch (err) {
      console.error("Ошибка поиска", err);
    }
  };

  // 2. Функция получения рекомендаций
  const fetchRecs = async () => {
    try {
      const res = await axios.get(`${API_URL}/recommendations/${user.id}`);
      setRecommendations(res.data);
    } catch (err) {
      console.error("Ошибка рекомендаций", err);
    }
  };

  // 3. Функция оценки фильма (лайк)
  const handleRate = async (movieId, ratingValue) => {
    if (!user || !ratingValue) return;
    try {
      await axios.post(`${API_URL}/rate`, {
        user_id: user.id,
        movie_id: movieId,
        rating: parseFloat(ratingValue)
      });
      fetchRecs(); // Обновляем список рекомендаций сразу после лайка
    } catch (err) {
      alert("Ошибка при сохранении оценки");
    }
  };

  // Загружаем рекомендации при старте
  useEffect(() => {
    if (user) fetchRecs();
  }, [user]);

  if (!user) {
    return (
      <div style={authContainerStyle}>
        <div style={cardStyle}>
          <h2>{isRegistering ? 'Регистрация' : 'Вход в CinemaRec'}</h2>
          <form onSubmit={handleAuth} style={{ display: 'flex', flexDirection: 'column', gap: '10px'}}>
            <input
              placeholder="Логин (английский)"
              pattern="^[a-zA-Z0-9_]+$"
              title="Используйте только английские буквы, цифры и подчеркивание"
              onChange={e => setAuthData({...authData, username: e.target.value})}
              style={inputStyle}
              required
            />
            <input
              type="password"
              placeholder="Пароль (минимум 6 символов)"
              minLength="6"
              title="Длина минимум 6 символов"
              onChange={e => setAuthData({...authData, password: e.target.value})}
              style={inputStyle}
              required
            />
            <button type="submit" style={searchButtonStyle}>
              {isRegistering ? 'Создать аккаунт' : 'Войти'}
            </button>

            <button 
              type="button"
              onClick={() => setIsRegistering(!isRegistering)} 
              style={{background: 'none', border: 'none', color: 'blue', cursor: 'pointer', marginTop: '10px'}}
            >
              {isRegistering ? 'Уже есть аккаунт? Войти' : 'Нет аккаунта? Регистрация'}  
            </button>
          </form>
        </div>
      </div>
    );
  }

  return (
    <div style={{ padding: '20px', fontFamily: 'Arial, sans-serif', backgroundColor: '#f4f4f9', minHeight: '100vh' }}>
      <header style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '30px' }}>
        <h1 style={{ color: '#333', display: 'flex', alignItems: 'center', gap: '10px' }}>
          <Film color="#e11d48" /> CinemaRec
        </h1>
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <User size={20} />
          <span>Привет, {user.username}!</span>
          <button onClick={handleLogout}>Выйти</button>
        </div>
      </header>

      {/* Блок рекомендаций */}
      <section style={{ marginBottom: '40px' }}>
        <h2>Персональные рекомендации</h2>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(250px, 1fr))', gap: '20px' }}>
          {recommendations.length > 0 ? recommendations.map(movie => (
            <div key={movie.id} style={cardStyle}>
              <img
                src={movie.poster_url || "https://via.placeholder.com/200x300?text=No+Poster"}
                alt={movie.title}
                onError={(e) => { e.target.src = "https://via.placeholder.com/200x300?text=No+Poster"; }}
                style={{width: '100%', height: '300px', objectFit: 'cover', borderRadius: '8px'}}
              />
              <h3 
                style={{ cursor: 'pointer', color: '#3b82f6', textDecoration: 'underline' }} 
                onClick={() => fetchStaff(movie.id)}
                title="Нажмите, чтобы увидеть актеров"
              >
                {movie.title}
              </h3>

              {/* Блок актеров */}
              {selectedStaff[movie.id] ? (
                <div style={{ 
                  fontSize: '0.85em', 
                  backgroundColor: '#f9f9f9', 
                  padding: '10px', 
                  borderRadius: '8px', 
                  marginBottom: '10px',
                  borderLeft: '4px solid #3b82f6' 
                }}>
                  {/* Режиссеры */}
                  {selectedStaff[movie.id].directors?.length > 0 && (
                    <div style={{ marginBottom: '5px' }}>
                      <b>Режиссер:</b> {selectedStaff[movie.id].directors.map(d => d.nameRu || d.nameEn).join(', ')}
                    </div>
                  )}
                  
                  {/* Актеры */}
                  {selectedStaff[movie.id].actors?.length > 0 && (
                    <div>
                      <b>В ролях:</b> {selectedStaff[movie.id].actors.slice(0, 5).map(a => 
                        `${a.nameRu || a.nameEn}${a.description ? ` (${a.description})` : ''}`
                      ).join(', ')}
                      {selectedStaff[movie.id].actors.length > 5 && ' и др.'}
                    </div>
                  )}
                </div>
              ) : null}
              <p style={{ fontSize: '0.8em', color: '#666' }}>{movie.genres}</p>
              
              <div style={{ 
                margin: '10px 0', 
                display: 'flex', 
                alignItems: 'center', 
                gap: '10px',
                justifyContent: 'space-between'
              }}>
                <span style={{ color: '#000000' }}>⭐ <b>{movie.average_rating || "0.0"}</b>
                  <small>({movie.votes || 0} голосов)</small>
                </span>
                <span style={{ color: '#000000'}}>IMDb: <b>{movie.imdb_rating || 0}</b></span>
              </div>


              <select
                onChange={(e) => handleRate(movie.id, e.target.value)}
                style={{ padding: '8px', borderRadius: '5px', border: '1px solid #e11d48', cursor: 'pointer'}}
              >
                <option value="">Оценить (1-10)</option>
                {[...Array(10)].map((_, i) => (
                  <option key={i+1} value={i+1}>{i + 1}</option>
                ))}
              </select>

            </div>
          )) : <p>Оцените несколько фильмов, чтобы получить рекомендации!</p>}
        </div>
      </section>

      <hr />

      {/* Блок поиска */}
      <section style={{ marginTop: '40px' }}>
        <h2>Найти фильм</h2>
        <form onSubmit={handleSearch} style={{ marginBottom: '20px', display: 'flex', gap: '10px' }}>
          <input 
            type="text" 
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Введите название фильма..." 
            style={{ padding: '10px', width: '300px', borderRadius: '5px', border: '1px solid #ccc' }}
          />
          <button type="submit" style={searchButtonStyle}><Search size={18} /> Найти</button>
        </form>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(250px, 1fr))', gap: '20px' }}>
          {movies.map(movie => (
            <div key={movie.id} style={cardStyle}>
              <img
                src={movie.poster_url || "https://via.placeholder.com/200x300?text=No+Poster"}
                alt={movie.title}
                onError={(e) => { e.target.src = "https://via.placeholder.com/200x300?text=No+Poster"; }}
                style={{width: '100%', height: '300px', objectFit: 'cover', borderRadius: '8px'}}
              />
              <h3 
                style={{ cursor: 'pointer', color: '#3b82f6', textDecoration: 'underline' }} 
                onClick={() => fetchStaff(movie.id)}
                title="Нажмите, чтобы увидеть актеров"
              >
                {movie.title}
              </h3>

              {/* Блок актеров */}
              {selectedStaff[movie.id] ? (
                <div style={{ 
                  fontSize: '0.85em', 
                  backgroundColor: '#f9f9f9', 
                  padding: '10px', 
                  borderRadius: '8px', 
                  marginBottom: '10px',
                  borderLeft: '4px solid #3b82f6' 
                }}>
                  {/* Режиссеры */}
                  {selectedStaff[movie.id].directors?.length > 0 && (
                    <div style={{ marginBottom: '5px' }}>
                      <b>Режиссер:</b> {selectedStaff[movie.id].directors.map(d => d.nameRu || d.nameEn).join(', ')}
                    </div>
                  )}
                  
                  {/* Актеры */}
                  {selectedStaff[movie.id].actors?.length > 0 && (
                    <div>
                      <b>В ролях:</b> {selectedStaff[movie.id].actors.slice(0, 5).map(a => 
                        `${a.nameRu || a.nameEn}${a.description ? ` (${a.description})` : ''}`
                      ).join(', ')}
                      {selectedStaff[movie.id].actors.length > 5 && ' и др.'}
                    </div>
                  )}
                </div>
              ) : null}
              <p style={{ fontSize: '0.8em', color: '#666' }}>{movie.genres}</p>
              
              <div style={{ 
                margin: '10px 0', 
                display: 'flex', 
                alignItems: 'center', 
                gap: '10px',
                justifyContent: 'space-between'
              }}>
                <span style={{ color: '#000000' }}>⭐ <b>{movie.average_rating || "0.0"}</b>
                  <small>({movie.votes || 0} голосов)</small>
                </span>
                <span style={{ color: '#000000'}}>IMDb: <b>{movie.imdb_rating || 0}</b></span>
              </div>

              <select
                onChange={(e) => handleRate(movie.id, e.target.value)}
                style={{ padding: '8px', borderRadius: '5px', border: '1px solid #e11d48', cursor: 'pointer'}}
              >
                <option value="">Оценить (1-10)</option>
                {[...Array(10)].map((_, i) => (
                  <option key={i+1} value={i+1}>{i + 1}</option>
                ))}
              </select>
              
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}

// Простые стили для карточек
const cardStyle = {
  backgroundColor: '#fff',
  padding: '15px',
  borderRadius: '10px',
  boxShadow: '0 4px 6px rgba(0,0,0,0.1)',
  display: 'flex',
  flexDirection: 'column',
  justifyContent: 'space-between'
};

const likeButtonStyle = {
  marginTop: '10px',
  padding: '8px',
  backgroundColor: '#e11d48',
  color: 'white',
  border: 'none',
  borderRadius: '5px',
  cursor: 'pointer',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  gap: '5px'
};

const searchButtonStyle = {
  padding: '10px 20px',
  backgroundColor: '#3b82f6',
  color: 'white',
  border: 'none',
  borderRadius: '5px',
  cursor: 'pointer',
  display: 'flex',
  alignItems: 'center',
  gap: '5px'
};

const authContainerStyle = {
  display: 'flex',
  justifyContent: 'center',
  alignItems: 'center',
  height: '100vh',
  backgroundColor: '#f0f2f5'
};

const inputStyle = {
  width: '200px',
  padding: '10px',
  borderRadius: '5px',
  border: '1px solid #ccc'
};

export default App;