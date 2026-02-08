import React, { useState, useEffect } from 'react';
import axios from 'axios';
import { Search, Film, Sliders, X, Menu, Sparkles, Star } from 'lucide-react';

const API_URL = "http://127.0.0.1:8000";

function App() {
  // --- STATE ---
  const [movies, setMovies] = useState([]);
  const [recommendations, setRecommendations] = useState([]);
  const [search, setSearch] = useState("");
  
  // Новое состояние для поиска по смыслу (через веса)
  const [smartSearchQuery, setSmartSearchQuery] = useState(""); 
  
  const [user, setUser] = useState(JSON.parse(localStorage.getItem('user')));
  const [isSidebarOpen, setIsSidebarOpen] = useState(true);
  const [authMode, setAuthMode] = useState('login'); 
  const [authData, setAuthData] = useState({ username: '', password: '' });
  
  const [weights, setWeights] = useState({
    genres: 5,
    staff: 2,
    description: 8
  });

  const [selectedStaff, setSelectedStaff] = useState({});
  const [showDescriptions, setShowDescriptions] = useState({});

  // --- 1. COLD START (ИСПРАВЛЕНИЕ) ---
  // Загружаем фильмы сразу при открытии страницы
  useEffect(() => {
    const fetchInitialMovies = async () => {
      try {
        // Если пользователь не залогинен, используем ID 0 (бэкенд должен вернуть рандом)
        const userId = user ? user.id : 0; 
        const res = await axios.get(`${API_URL}/recommendations/${userId}`);
        setMovies(res.data);
      } catch (err) {
        console.error("Ошибка загрузки начальных фильмов", err);
      }
    };
    fetchInitialMovies();
  }, [user]); // Перезапустится, если пользователь войдет/выйдет

  // --- API FUNCTIONS ---

  // Обычный поиск по названию (TF-IDF title)
  const handleSearch = async (e) => {
    e.preventDefault();
    if (!search.trim()) return;
    try {
      const res = await axios.get(`${API_URL}/search?title=${search}&user_id=${user ? user.id : 0}`); // Исправил query -> title (как в main.py)
      setMovies(res.data);
      setRecommendations([]); 
    } catch (err) {
      console.error("Ошибка поиска", err);
    }
  };

  // УМНЫЙ ПОИСК (ПО ВЕСАМ И ОПИСАНИЮ)
  const handleSmartSearch = async () => {
    if (!smartSearchQuery.trim() && movies.length === 0) {
        alert("Введите тему или выберите фильмы");
        return;
    }
    
    try {
        // Мы отправляем пустой список base_movie_ids, но заполняем manual_keywords
        // Бэкенд поймет это и будет искать только по описанию + учитывать веса
        const res = await axios.post(`${API_URL}/recommendations/custom`, {
            user_id: user ? user.id : 0,
            base_movie_ids: [], 
            weights: weights,
            manual_keywords: smartSearchQuery // Текст из нового инпута
        });
        setRecommendations(res.data);
        // Прокручиваем к рекомендациям
        document.getElementById('rec-section')?.scrollIntoView({ behavior: 'smooth' });
    } catch (err) {
        console.error("Ошибка умного поиска", err);
    }
  };

  // Поиск похожих на конкретный фильм
  const fetchRecommendations = async (baseMovieId) => {
    try {
      const res = await axios.post(`${API_URL}/recommendations/custom`, {
        user_id: user ? user.id : 0,
        base_movie_ids: [baseMovieId],
        weights: weights,
        manual_keywords: "" 
      });
      setRecommendations(res.data);
      document.getElementById('rec-section')?.scrollIntoView({ behavior: 'smooth' });
    } catch (err) {
      console.error("Ошибка рекомендаций", err);
    }
  };

  const fetchStaff = async (movieId) => {
    if (selectedStaff[movieId]) {
        const newStaff = { ...selectedStaff };
        delete newStaff[movieId];
        setSelectedStaff(newStaff);
        return;
    }
    try {
      const res = await axios.get(`${API_URL}/movie/${movieId}/staff`);
      // Проверяем формат ответа (если там сразу массив или внутри поля)
      const data = Array.isArray(res.data) ? res.data : [];
      
      const staff = {
        actors: data.filter(s => s.professionKey === 'ACTOR').slice(0, 5),
        directors: data.filter(s => s.professionKey === 'DIRECTOR')
      };
      setSelectedStaff(prev => ({ ...prev, [movieId]: staff }));
    } catch (err) {
      console.error("Ошибка загрузки состава", err);
    }
  };

  // --- AUTH HANDLERS ---
  const handleAuth = async (e) => {
    e.preventDefault();
    try {
      const endpoint = authMode === 'login' ? '/login' : '/register';
      const res = await axios.post(`${API_URL}${endpoint}`, authData);
      if (authMode === 'login') {
        localStorage.setItem('user', JSON.stringify(res.data));
        setUser(res.data);
      } else {
        alert("Регистрация успешна! Теперь войдите.");
        setAuthMode('login');
      }
    } catch (err) {
      alert("Ошибка авторизации");
    }
  };

  const logout = () => {
    localStorage.removeItem('user');
    setUser(null);
  };

  // --- HELPERS ---
  const toggleDescription = (id) => {
    setShowDescriptions(prev => ({ ...prev, [id]: !prev[id] }));
  };

  // Функция для возврата на "Главную" с топовыми фильмами
  const handleGoHome = async () => {
    // 1. Очищаем все поля поиска
    setSearch("");
    setSmartSearchQuery("");
    setRecommendations([]); // Скрываем блок AI рекомендаций

    try {
      // 2. Запрашиваем фильмы с высоким рейтингом
      // Мы можем использовать существующий эндпоинт или создать новый.
      // Если в бэкенде /recommendations/{id} возвращает рандом, 
      // давай добавим вызов, который вернет именно ТОП.
      const res = await axios.get(`${API_URL}/recommendations/${user ? user.id : 0}`);
      setMovies(res.data);
      
      // Скроллим наверх для удобства
      window.scrollTo({ top: 0, behavior: 'smooth' });
    } catch (err) {
      console.error("Ошибка при возврате на главную", err);
    }
  };

  const handleRate = async (movieId, score) => {
    if (!user) {
      alert("Войдите в систему, чтобы оценивать фильмы");
      return;
    }

    try {
      // Отправляем на бэк
      const res = await axios.post(`${API_URL}/rate`, {
        user_id: user.id,
        movie_id: movieId,
        rating: score
      });

      // Обновляем состояние локально, чтобы звезды сразу закрасились
      const updateList = (list) => list.map(m => 
        m.id === movieId ? { 
          ...m, 
          user_rating: score, // Ставим "нашу" оценку для звезд
          average_rating: res.data.average_rating, // Обновляем общий балл сайта
          votes: res.data.votes 
        } : m
      );

      setMovies(prev => updateList(prev));
      setRecommendations(prev => updateList(prev));

    } catch (err) {
      console.error("Ошибка при оценке:", err);
    }
  };

  return (
    <div style={layoutStyles.container}>
      
      {/* 1. SIDEBAR */}
      <aside style={{ 
          ...layoutStyles.sidebar, 
          width: isSidebarOpen ? '320px' : '0',
          padding: isSidebarOpen ? '20px' : '0',
          opacity: isSidebarOpen ? 1 : 0
      }}>
        <div style={layoutStyles.sidebarHeader}>
          <h2 style={{margin: 0, display: 'flex', alignItems: 'center', gap: '10px'}}>
             <Sliders size={20}/> Конструктор
          </h2>
        </div>

        {/* 1. ОБЫЧНЫЙ ПОИСК */}
        <div style={layoutStyles.controlGroup}>
          <label style={layoutStyles.label}>Быстрый поиск</label>
          <form onSubmit={handleSearch} style={{display: 'flex', gap: '5px'}}>
            <input 
              style={layoutStyles.input}
              value={search}
              onChange={e => setSearch(e.target.value)}
              placeholder="Название (напр. Матрица)..."
            />
            <button type="submit" style={layoutStyles.iconButton}><Search size={16}/></button>
          </form>
        </div>

        <hr style={layoutStyles.divider} />

        {/* 2. НАСТРОЙКА ВЕСОВ */}
        <div style={layoutStyles.controlGroup}>
          <h4 style={layoutStyles.subHeader}>Настройки нейросети</h4>
          
          <div style={layoutStyles.sliderContainer}>
            <div style={layoutStyles.sliderLabel}>
              <span>Жанры</span> <span style={layoutStyles.badge}>{weights.genres}</span>
            </div>
            <input type="range" min="0" max="10" step="0.5" value={weights.genres}
              onChange={e => setWeights({...weights, genres: parseFloat(e.target.value)})}
              style={layoutStyles.slider} />
          </div>

          <div style={layoutStyles.sliderContainer}>
            <div style={layoutStyles.sliderLabel}>
              <span>Сюжет (Описание)</span> <span style={layoutStyles.badge}>{weights.description}</span>
            </div>
            <input type="range" min="0" max="10" step="0.5" value={weights.description}
              onChange={e => setWeights({...weights, description: parseFloat(e.target.value)})}
              style={layoutStyles.slider} />
          </div>

          <div style={layoutStyles.sliderContainer}>
            <div style={layoutStyles.sliderLabel}>
              <span>Актеры/Режиссеры</span> <span style={layoutStyles.badge}>{weights.staff}</span>
            </div>
            <input type="range" min="0" max="10" step="0.5" value={weights.staff}
              onChange={e => setWeights({...weights, staff: parseFloat(e.target.value)})}
              style={layoutStyles.slider} />
          </div>
        </div>

        {/* 3. УМНЫЙ ПОИСК ПО ТЕМЕ */}
        <div style={{...layoutStyles.controlGroup, background: '#f0f9ff', padding: '10px', borderRadius: '8px'}}>
            <label style={{...layoutStyles.label, color: '#0369a1'}}>
                <Sparkles size={14} style={{marginRight: '5px', display:'inline'}}/>
                Поиск по смыслу
            </label>
            <textarea 
                style={{...layoutStyles.input, minHeight: '60px', marginBottom: '10px', fontSize: '13px'}}
                placeholder="Например: грустный фильм про космос и одиночество..."
                value={smartSearchQuery}
                onChange={e => setSmartSearchQuery(e.target.value)}
            />
            <button onClick={handleSmartSearch} style={layoutStyles.magicButton}>
                Подобрать по весам
            </button>
        </div>

      </aside>


      {/* 2. MAIN CONTENT */}
      <main style={layoutStyles.main}>
        <header style={layoutStyles.topBar}>
          <button onClick={() => setIsSidebarOpen(!isSidebarOpen)} style={layoutStyles.menuButton}>
            {isSidebarOpen ? <X size={24}/> : <Menu size={24}/>}
          </button>
          <h1 
            onClick={handleGoHome} 
            style={{
              fontSize: '20px', 
              margin: 0, 
              color: '#333', 
              cursor: 'pointer', // Делаем курсор "ручкой"
              userSelect: 'none', // Чтобы текст не выделялся случайно
              display: 'flex',
              alignItems: 'center',
              gap: '8px'
            }}
            onMouseOver={(e) => e.target.style.color = '#2563eb'} // Эффект наведения
            onMouseOut={(e) => e.target.style.color = '#333'}
          >
            <Film size={24} color="#2563eb" /> Movie Matcher AI
          </h1>
          <div style={{marginLeft: 'auto'}}>
            {user ? (
              <div style={{display: 'flex', alignItems: 'center', gap: '15px'}}>
                <span style={{fontWeight: 'bold', color: '#2563eb'}}>{user.username}</span>
                <button onClick={logout} style={layoutStyles.smallBtn}>Выйти</button>
              </div>
            ) : (
              <div style={{display: 'flex', gap: '5px'}}>
                <input placeholder="Логин" value={authData.username} onChange={e=>setAuthData({...authData, username: e.target.value})} style={layoutStyles.smallInput}/>
                 <input placeholder="Пароль" type="password" value={authData.password} onChange={e=>setAuthData({...authData, password: e.target.value})} style={layoutStyles.smallInput}/>
                <button onClick={handleAuth} style={layoutStyles.primaryBtn}>Войти</button>
              </div>
            )}
          </div>
        </header>

        <div style={layoutStyles.contentArea}>
          
          {/* Секция 1: Обычная выдача (или результаты поиска) */}
          {movies.length > 0 && (
            <div style={layoutStyles.section}>
              <h3 style={layoutStyles.sectionTitle}>
                  {search ? `Поиск: "${search}"` : "Популярное / Случайное"}
              </h3>
              <div style={layoutStyles.grid}>
                {movies.map(movie => (
                  <MovieCard 
                    key={movie.id} 
                    movie={movie} 
                    onRate={handleRate}
                    onRecommend={() => fetchRecommendations(movie.id)}
                    onToggleStaff={() => fetchStaff(movie.id)}
                    onToggleDesc={() => toggleDescription(movie.id)}
                    staffData={selectedStaff[movie.id]}
                    showDesc={showDescriptions[movie.id]}
                    isRecommendation={false}
                  />
                ))}
              </div>
            </div>
          )}

          {/* Секция 2: Рекомендации (СЮДА СКРОЛЛИМСЯ) */}
          {recommendations.length > 0 && (
            <div id="rec-section" style={{...layoutStyles.section, background: '#f8fafc', padding: '20px', borderRadius: '15px', marginTop: '30px', border: '2px solid #e2e8f0'}}>
              <h3 style={{...layoutStyles.sectionTitle, color: '#2563eb', display: 'flex', alignItems: 'center', gap: '10px'}}>
                 <Sparkles size={20} /> AI Подборка
              </h3>
              <div style={layoutStyles.grid}>
                {recommendations.map(movie => (
                   <MovieCard 
                   key={movie.id} 
                   movie={movie} 
                   onRecommend={() => fetchRecommendations(movie.id)} 
                   onToggleStaff={() => fetchStaff(movie.id)}
                   onToggleDesc={() => toggleDescription(movie.id)}
                   staffData={selectedStaff[movie.id]}
                   showDesc={showDescriptions[movie.id]}
                   isRecommendation={true}
                 />
                ))}
              </div>
            </div>
          )}

          {movies.length === 0 && recommendations.length === 0 && (
            <div style={layoutStyles.emptyState}>
              <Film size={64} color="#ccc"/>
              <p>Загрузка фильмов...</p>
            </div>
          )}

        </div>
      </main>
    </div>
  );
}

const StarRating = ({ userRating, onRate }) => {
  const [hover, setHover] = React.useState(0);

  return (
    <div style={{ display: 'flex', gap: '2px', marginBottom: '10px' }}>
      {[...Array(10)].map((_, index) => {
        const ratingValue = index + 1;
        // Используем константу isActive для всего: и для цвета, и для заливки
        const isActive = ratingValue <= (hover || userRating || 0);
        
        return (
          <button
            key={index}
            style={{
              background: 'none', border: 'none', cursor: 'pointer', padding: 0,
              color: isActive ? '#f59e0b' : '#e2e8f0', // Исправлено: теперь цвет зависит от isActive
              transition: 'color 0.2s'
            }}
            onClick={() => onRate(ratingValue)}
            onMouseEnter={() => setHover(ratingValue)}
            onMouseLeave={() => setHover(0)}
          >
            <Star size={14} fill={isActive ? '#f59e0b' : 'none'} />
          </button>
        );
      })}
    </div>
  );
};

// --- КОМПОНЕНТ КАРТОЧКИ ---
const MovieCard = ({ movie, onRecommend, onToggleStaff, onToggleDesc, staffData, showDesc, isRecommendation }) => {
    return (
        <div style={cardStyle.wrapper}>
            <div style={{position: 'relative'}}>
                 <img 
                    src={movie.poster_url || "https://via.placeholder.com/300x450"} 
                    alt={movie.title} 
                    onError={(e) => { e.target.src = "https://via.placeholder.com/300x450?text=Нет+постера"; }}
                    style={cardStyle.image} 
                 />
                 {movie.match_reason && (
                     <div style={cardStyle.matchReason}>
                         {movie.match_reason.split('|').map((tag, i) => (
                             <div key={i} style={cardStyle.tag}>{tag.trim()}</div>
                         ))}
                     </div>
                 )}
            </div>
           
            <div style={cardStyle.content}>
                <h4 style={cardStyle.title}>{movie.title}</h4>
                <div style={cardStyle.meta}>
                    <span style={{color: '#f59e0b', fontWeight: 'bold'}}>★ {movie.imdb_rating}</span>
                    <span style={{color: '#64748b', fontSize: '11px', textAlign: 'right'}}>{movie.genres ? movie.genres.split(' ').slice(0, 2).join(', ') : ''}</span>
                </div>

                <div style={cardStyle.actions}>
                    <button onClick={onToggleDesc} style={cardStyle.textBtn}>Сюжет</button>
                    <button onClick={onToggleStaff} style={cardStyle.textBtn}>Актеры</button>
                </div>

                {/* ПЕРСОНАЛЬНЫЕ ЗВЕЗДЫ */}
                <div style={{margin: '10px 0'}}>
                  <div style={{fontSize: '10px', color: '#94a3b8', marginBottom: '2px'}}>ВАША ОЦЕНКА:</div>
                  <StarRating 
                    userRating={movie.user_rating} 
                    onRate={(score) => onRate(movie.id, score)} 
                  />
                </div>

                <div style={cardStyle.actions}>
                    <button onClick={onToggleDesc} style={cardStyle.textBtn}>Сюжет</button>
                    <button onClick={onToggleStaff} style={cardStyle.textBtn}>Актеры</button>
                </div>

                {showDesc && (
                    <p style={cardStyle.description}>{movie.description || "Описание отсутствует"}</p>
                )}

                {staffData && (
                    <div style={cardStyle.staffList}>
                        {staffData.directors.length > 0 && (
                            <div style={{marginBottom: '5px'}}>
                                <strong>Реж:</strong> {staffData.directors.map(d=>d.nameRu).join(', ')}
                            </div>
                        )}
                        {staffData.actors.length > 0 && (
                            <div>
                                <strong>Акт:</strong> {staffData.actors.map(a=>a.nameRu).join(', ')}
                            </div>
                        )}
                    </div>
                )}

                <button onClick={onRecommend} style={isRecommendation ? cardStyle.recButtonSecondary : cardStyle.recButton}>
                   {isRecommendation ? "Еще похожее" : "Найти похожее"}
                </button>
            </div>
        </div>
    )
}

// --- СТИЛИ ---
const layoutStyles = {
  container: { display: 'flex', height: '100vh', width: '100%', fontFamily: "'Inter', sans-serif", backgroundColor: '#f1f5f9', overflow: 'hidden' },
  sidebar: { backgroundColor: '#ffffff', borderRight: '1px solid #e2e8f0', display: 'flex', flexDirection: 'column', transition: 'all 0.3s ease', overflowY: 'auto', flexShrink: 0, zIndex: 10 },
  sidebarHeader: { marginBottom: '20px', color: '#1e293b' },
  controlGroup: { marginBottom: '20px' },
  label: { display: 'block', marginBottom: '8px', fontSize: '14px', fontWeight: '600', color: '#475569' },
  subHeader: { margin: '0 0 10px 0', fontSize: '14px', textTransform: 'uppercase', letterSpacing: '0.5px', color: '#64748b' },
  input: { width: '100%', padding: '10px', borderRadius: '6px', border: '1px solid #cbd5e1', outline: 'none', boxSizing: 'border-box' },
  iconButton: { background: '#3b82f6', color: 'white', border: 'none', borderRadius: '6px', padding: '0 10px', cursor: 'pointer' },
  divider: { border: 'none', borderTop: '1px solid #e2e8f0', margin: '15px 0' },
  sliderContainer: { marginBottom: '15px' },
  sliderLabel: { display: 'flex', justifyContent: 'space-between', marginBottom: '5px', fontSize: '13px', color: '#334155' },
  badge: { background: '#eff6ff', color: '#3b82f6', padding: '2px 6px', borderRadius: '4px', fontWeight: 'bold', fontSize: '12px' },
  slider: { width: '100%', cursor: 'pointer' },
  main: { flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', position: 'relative' },
  topBar: { height: '60px', backgroundColor: '#ffffff', borderBottom: '1px solid #e2e8f0', display: 'flex', alignItems: 'center', padding: '0 20px', gap: '20px' },
  menuButton: { background: 'transparent', border: 'none', cursor: 'pointer', color: '#64748b' },
  contentArea: { flex: 1, overflowY: 'auto', padding: '20px' },
  section: { marginBottom: '30px' },
  sectionTitle: { fontSize: '18px', marginBottom: '15px', color: '#1e293b' },
  grid: { display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: '20px' },
  emptyState: { height: '100%', display: 'flex', flexDirection: 'column', justifyContent: 'center', alignItems: 'center', color: '#94a3b8' },
  smallInput: { padding: '6px', borderRadius: '4px', border: '1px solid #ccc', fontSize: '12px' },
  primaryBtn: { padding: '6px 12px', background: '#3b82f6', color: 'white', border: 'none', borderRadius: '4px', cursor: 'pointer', fontSize: '12px' },
  smallBtn: { padding: '6px 12px', background: '#ef4444', color: 'white', border: 'none', borderRadius: '4px', cursor: 'pointer', fontSize: '12px' },
  magicButton: { width: '100%', padding: '10px', background: 'linear-gradient(135deg, #3b82f6 0%, #8b5cf6 100%)', color: 'white', border: 'none', borderRadius: '6px', cursor: 'pointer', fontWeight: 'bold', marginTop: '5px' }
};

const cardStyle = {
  wrapper: { backgroundColor: 'white', borderRadius: '12px', overflow: 'hidden', boxShadow: '0 4px 6px -1px rgba(0, 0, 0, 0.1)', transition: 'transform 0.2s', display: 'flex', flexDirection: 'column', height: '100%' },
  image: { width: '100%', height: '320px', objectFit: 'cover' },
  content: { padding: '12px', display: 'flex', flexDirection: 'column', flex: 1 },
  title: { margin: '0 0 8px 0', fontSize: '15px', lineHeight: '1.2' },
  meta: { display: 'flex', justifyContent: 'space-between', fontSize: '12px', marginBottom: '10px', alignItems: 'center' },
  actions: { display: 'flex', gap: '10px', marginBottom: '10px' },
  textBtn: { background: 'none', border: 'none', color: '#3b82f6', cursor: 'pointer', fontSize: '12px', padding: 0, textDecoration: 'underline' },
  description: { fontSize: '12px', color: '#475569', background: '#f1f5f9', padding: '8px', borderRadius: '6px', marginBottom: '10px' },
  staffList: { fontSize: '11px', color: '#475569', background: '#fff7ed', padding: '8px', borderRadius: '6px', marginBottom: '10px' },
  recButton: { marginTop: 'auto', width: '100%', padding: '10px', background: '#10b981', color: 'white', border: 'none', borderRadius: '8px', cursor: 'pointer', fontWeight: '600' },
  recButtonSecondary: { marginTop: 'auto', width: '100%', padding: '10px', background: '#6366f1', color: 'white', border: 'none', borderRadius: '8px', cursor: 'pointer', fontWeight: '600' },
  matchReason: { position: 'absolute', bottom: '0', left: '0', right: '0', background: 'rgba(0,0,0,0.8)', padding: '6px', display: 'flex', flexWrap: 'wrap', gap: '4px' },
  tag: { background: '#2563eb', color: 'white', fontSize: '10px', padding: '2px 6px', borderRadius: '4px' }
};

export default App;
