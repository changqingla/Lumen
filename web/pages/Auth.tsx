import React, { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { authAPI } from '@/lib/api';
import { ArrowRight, Loader2, Cpu, Zap, Activity } from 'lucide-react';

// ==========================================
// 🎨 Art Component: Neural Network Background
// ==========================================
const NeuralBackground = () => {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    let width = canvas.width = window.innerWidth;
    let height = canvas.height = window.innerHeight;
    
    // Particle configuration
    const particleCount = 60;
    const connectionDistance = 150;
    const mouseDistance = 200;

    const particles: Array<{x: number, y: number, vx: number, vy: number, size: number}> = [];
    
    // Mouse position
    const mouse = { x: -1000, y: -1000 };

    // Initialize particles
    for (let i = 0; i < particleCount; i++) {
      particles.push({
        x: Math.random() * width,
        y: Math.random() * height,
        vx: (Math.random() - 0.5) * 0.5,
        vy: (Math.random() - 0.5) * 0.5,
        size: Math.random() * 2 + 1
      });
    }

    const handleResize = () => {
      width = canvas.width = window.innerWidth;
      height = canvas.height = window.innerHeight;
    };

    const handleMouseMove = (e: MouseEvent) => {
      mouse.x = e.clientX;
      mouse.y = e.clientY;
    };

    window.addEventListener('resize', handleResize);
    window.addEventListener('mousemove', handleMouseMove);

    // Animation Loop
    const animate = () => {
      ctx.clearRect(0, 0, width, height);
      
      // Update and draw particles
      particles.forEach((p, i) => {
        // Move
        p.x += p.vx;
        p.y += p.vy;

        // Bounce edges
        if (p.x < 0 || p.x > width) p.vx *= -1;
        if (p.y < 0 || p.y > height) p.vy *= -1;

        // Mouse interaction (repel)
        const dx = mouse.x - p.x;
        const dy = mouse.y - p.y;
        const dist = Math.sqrt(dx * dx + dy * dy);
        
        if (dist < mouseDistance) {
          const forceDirectionX = dx / dist;
          const forceDirectionY = dy / dist;
          const force = (mouseDistance - dist) / mouseDistance;
          p.vx -= forceDirectionX * force * 0.05;
          p.vy -= forceDirectionY * force * 0.05;
        }

        // Draw particle
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.size, 0, Math.PI * 2);
        ctx.fillStyle = 'rgba(6, 182, 212, 0.5)'; // Cyan
        ctx.fill();

        // Draw connections
        for (let j = i + 1; j < particles.length; j++) {
          const p2 = particles[j];
          const dx2 = p.x - p2.x;
          const dy2 = p.y - p2.y;
          const dist2 = Math.sqrt(dx2 * dx2 + dy2 * dy2);

          if (dist2 < connectionDistance) {
            ctx.beginPath();
            ctx.strokeStyle = `rgba(6, 182, 212, ${1 - dist2 / connectionDistance})`;
            ctx.lineWidth = 0.5;
            ctx.moveTo(p.x, p.y);
            ctx.lineTo(p2.x, p2.y);
            ctx.stroke();
          }
        }
      });

      requestAnimationFrame(animate);
    };

    animate();

    return () => {
      window.removeEventListener('resize', handleResize);
      window.removeEventListener('mousemove', handleMouseMove);
    };
  }, []);

  return <canvas ref={canvasRef} className="absolute inset-0 z-0 bg-black" />;
};

// ==========================================
// 🚀 Main Auth Component
// ==========================================
type Tab = 'login' | 'register' | 'reset';

export default function Auth() {
  const navigate = useNavigate();
  const [tab, setTab] = useState<Tab>('login');
  
  // Form States
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [name, setName] = useState('');
  const [confirm, setConfirm] = useState('');
  const [verificationCode, setVerificationCode] = useState('');
  
  // UI States
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);
  
  // Verification Code Logic
  const [timer, setTimer] = useState(0);
  const [sendingCode, setSendingCode] = useState(false);

  // Timer Effect
  useEffect(() => {
    let interval: NodeJS.Timeout;
    if (timer > 0) {
      interval = setInterval(() => {
        setTimer((prev) => prev - 1);
      }, 1000);
    }
    return () => clearInterval(interval);
  }, [timer]);

  const validateEmail = (v: string) => /.+@.+\..+/.test(v);

  const handleSendCode = async () => {
    setError(null);
    if (!validateEmail(email)) {
      setError('// 错误: 无效的通讯地址');
      return;
    }
    
    setSendingCode(true);
    try {
      // Determine type based on current tab
      const type = tab === 'reset' ? 'reset' : 'register';
      await authAPI.sendVerificationCode(email, type);
      setTimer(60);
      setSuccessMsg('>> 信号已发射。请检查您的接收终端。');
    } catch (err: any) {
      const msg = err.message || '';
      setError(msg.startsWith('//') ? msg : `// 错误: ${msg || '信号链路中断'}`);
    } finally {
      setSendingCode(false);
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setSuccessMsg(null);
    setLoading(true);
    
    try {
      if (!validateEmail(email)) throw new Error('// 错误: 邮箱格式无效');
      if (password.length < 6) throw new Error('// 错误: 密钥长度不足 (min 6)');
      
      if (tab === 'register') {
        if (!name.trim()) throw new Error('// 错误: 缺失身份标识 (昵称)');
        if (name.length > 8) throw new Error('// 错误: 用户名不能超过8个字符');
        if (password !== confirm) throw new Error('// 错误: 密钥确认失败');
        if (!verificationCode) throw new Error('// 错误: 缺失验证信号');
        if (verificationCode.length < 6) throw new Error('// 错误: 验证信号格式错误');

        await authAPI.register(email, password, name, verificationCode);
        
        setTab('login');
        setPassword('');
        setConfirm('');
        setVerificationCode('');
        setTimer(0);
        setSuccessMsg('>> 身份创建成功。正在初始化...');
      } else if (tab === 'reset') {
        if (password !== confirm) throw new Error('// 错误: 密钥确认失败');
        if (!verificationCode) throw new Error('// 错误: 缺失验证信号');
        if (verificationCode.length < 6) throw new Error('// 错误: 验证信号格式错误');

        await authAPI.resetPassword(email, password, verificationCode);

        setTab('login');
        setPassword('');
        setConfirm('');
        setVerificationCode('');
        setTimer(0);
        setSuccessMsg('>> 密钥重置成功。请重新连接。');
      } else {
        const response = await authAPI.login(email, password);
        localStorage.setItem('auth_token', response.token);
        localStorage.setItem('auth_user', JSON.stringify(response.user));
        localStorage.setItem('userProfile', JSON.stringify(response.user));
        navigate('/');
      }
    } catch (err: any) {
      // 移除 "Error: " 前缀，保持极客风
      const msg = err.message.replace('Error: ', '');
      setError(msg.startsWith('//') ? msg : `// 错误: ${msg}`);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="relative w-full h-screen overflow-hidden bg-black text-white font-mono font-semibold selection:bg-cyan-500 selection:text-black">
      {/* 🌌 Dynamic Neural Background */}
      <NeuralBackground />

      {/* ⚡ Content Overlay */}
      <div className="absolute inset-0 z-10 flex flex-col md:flex-row">
        
        {/* Left: Typography Art (The "Brand" Zone) */}
        <div className="w-full md:w-1/2 p-10 md:p-20 flex flex-col justify-between pointer-events-none select-none relative overflow-hidden">
          <div className="absolute top-0 left-0 w-full h-full bg-gradient-to-b from-black/0 via-black/0 to-black/80 md:bg-gradient-to-r md:from-black/0 md:via-black/0 md:to-black/50"></div>
          
          <div className="z-20">
            <div className="flex items-center gap-2 text-cyan-500 mb-6 animate-pulse">
              <Cpu className="w-5 h-5" />
              <span className="text-xs tracking-[0.3em] uppercase">System Online</span>
            </div>
            <h1 className="text-6xl md:text-8xl font-black tracking-tighter leading-[0.9] mix-blend-difference">
              READ<br/>
              <span className="text-transparent bg-clip-text bg-gradient-to-r from-cyan-400 to-purple-600">ER</span>
              <span className="text-xl md:text-3xl font-light tracking-normal ml-2 opacity-50">.AI</span>
            </h1>
          </div>

          <div className="z-20 space-y-4 opacity-60 hidden md:block">
            <div className="flex items-center gap-4">
              <Activity className="w-4 h-4 text-cyan-400" />
              <div className="h-px bg-gray-700 flex-1"></div>
              <span className="text-xs">NEURAL LINK ACTIVE</span>
            </div>
            <p className="text-sm max-w-md leading-relaxed">
              进入下一代知识处理终端。<br/>
              让思维在数据流中涌现，捕捉灵感的每一个脉冲。
            </p>
          </div>
        </div>

        {/* Right: The "Interface" (Form) */}
        <div className="w-full md:w-1/2 h-full flex flex-col justify-center px-8 md:px-24 relative backdrop-blur-sm bg-black/40 md:bg-transparent border-l border-white/5">
          {/* Form Header */}
          <div className="mb-12">
            <div className="flex gap-8 text-sm tracking-widest border-b border-white/10 pb-4">
              <button 
                onClick={() => setTab('login')}
                className={`transition-colors duration-300 hover:text-cyan-400 ${tab === 'login' ? 'text-cyan-400 font-bold' : 'text-gray-400'}`}
              >
                [ 登 录 ]
              </button>
              <button 
                onClick={() => setTab('register')}
                className={`transition-colors duration-300 hover:text-purple-400 ${tab === 'register' ? 'text-purple-400 font-bold' : 'text-gray-400'}`}
              >
                [ 注 册 ]
              </button>
            </div>
          </div>

          {/* Form Inputs */}
          <form onSubmit={handleSubmit} className="space-y-8">
            {tab === 'register' && (
              <div className="group relative">
                <input
                  type="text"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  className="block w-full bg-transparent border-b border-gray-800 py-3 text-lg text-white focus:border-purple-500 focus:outline-none transition-colors peer placeholder-transparent"
                  placeholder="Identity"
                  id="name"
                />
                <label htmlFor="name" className="absolute left-0 -top-3.5 text-xs text-purple-500 transition-all peer-placeholder-shown:text-base peer-placeholder-shown:text-gray-400 peer-placeholder-shown:top-3 peer-focus:-top-3.5 peer-focus:text-xs peer-focus:text-purple-500">
                  用户代号 (昵称)
                </label>
              </div>
            )}

            <div className="group relative">
              <input
                type="text"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className={`block w-full bg-transparent border-b border-gray-800 py-3 text-lg text-white focus:outline-none transition-colors peer placeholder-transparent ${
                  tab === 'reset' ? 'focus:border-amber-500' : 'focus:border-cyan-500'
                }`}
                placeholder="Email"
                id="email"
              />
              <label htmlFor="email" className={`absolute left-0 -top-3.5 text-xs transition-all peer-placeholder-shown:text-base peer-placeholder-shown:text-gray-400 peer-placeholder-shown:top-3 peer-focus:-top-3.5 peer-focus:text-xs ${
                tab === 'reset' ? 'text-amber-500 peer-focus:text-amber-500' : 'text-cyan-500 peer-focus:text-cyan-500'
              }`}>
                神经链接 (邮箱)
              </label>
            </div>

            {(tab === 'register' || tab === 'reset') && (
              <div className="flex gap-4 items-end">
                <div className="group relative flex-1">
                  <input
                    type="text"
                    value={verificationCode}
                    onChange={(e) => setVerificationCode(e.target.value)}
                    className={`block w-full bg-transparent border-b border-gray-800 py-3 text-lg text-white focus:outline-none transition-colors peer placeholder-transparent tracking-[0.5em] text-center ${
                       tab === 'reset' ? 'focus:border-amber-500' : 'focus:border-cyan-500'
                    }`}
                    placeholder="Code"
                    id="code"
                  />
                  <label htmlFor="code" className={`absolute left-0 -top-3.5 text-xs transition-all peer-placeholder-shown:text-base peer-placeholder-shown:text-gray-400 peer-placeholder-shown:top-3 peer-focus:-top-3.5 peer-focus:text-xs ${
                    tab === 'reset' ? 'text-amber-500 peer-focus:text-amber-500' : 'text-gray-400 peer-focus:text-cyan-500'
                  }`}>
                    验证信标
                  </label>
                </div>
                <button
                  type="button"
                  onClick={handleSendCode}
                  disabled={sendingCode || timer > 0}
                  className={`pb-3 text-xs hover:opacity-80 disabled:text-gray-500 disabled:cursor-not-allowed transition-colors font-bold ${
                    tab === 'reset' ? 'text-amber-500' : 'text-cyan-500'
                  }`}
                >
                  {sendingCode ? '发射中...' : timer > 0 ? `${timer}秒后重试` : '[ 发送验证码 ]'}
                </button>
              </div>
            )}

            <div className="group relative">
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className={`block w-full bg-transparent border-b border-gray-800 py-3 text-lg text-white focus:outline-none transition-colors peer placeholder-transparent ${
                   tab === 'reset' ? 'focus:border-amber-500' : 'focus:border-cyan-500'
                }`}
                placeholder="Password"
                id="password"
              />
              <label htmlFor="password" className={`absolute left-0 -top-3.5 text-xs transition-all peer-placeholder-shown:text-base peer-placeholder-shown:text-gray-400 peer-placeholder-shown:top-3 peer-focus:-top-3.5 peer-focus:text-xs ${
                tab === 'reset' ? 'text-amber-500 peer-focus:text-amber-500' : 'text-cyan-500 peer-focus:text-cyan-500'
              }`}>
                {tab === 'reset' ? '重置密钥' : '通行密钥'}
              </label>
              {tab === 'login' && (
                <button
                  type="button"
                  onClick={() => setTab('reset')}
                  className="absolute right-0 bottom-3 text-xs text-gray-400 hover:text-amber-500 transition-colors"
                >
                  忘记密钥?
                </button>
              )}
            </div>

            {(tab === 'register' || tab === 'reset') && (
              <div className="group relative">
                <input
                  type="password"
                  value={confirm}
                  onChange={(e) => setConfirm(e.target.value)}
                  className={`block w-full bg-transparent border-b border-gray-800 py-3 text-lg text-white focus:outline-none transition-colors peer placeholder-transparent ${
                    tab === 'reset' ? 'focus:border-amber-500' : 'focus:border-purple-500'
                  }`}
                  placeholder="Confirm"
                  id="confirm"
                />
                <label 
                  htmlFor="confirm" 
                  className={`absolute left-0 -top-3.5 text-xs transition-all peer-placeholder-shown:text-base peer-placeholder-shown:text-gray-400 peer-placeholder-shown:top-3 peer-focus:-top-3.5 peer-focus:text-xs ${
                    tab === 'reset' ? 'text-amber-500 peer-focus:text-amber-500' : 'text-purple-500 peer-focus:text-purple-500'
                  }`}
                >
                  确认密钥
                </label>
              </div>
            )}

            {/* Terminal Output (Errors/Success) */}
            <div className="min-h-[24px] font-mono text-xs">
              {error && <div className="text-red-500 animate-pulse">{error}</div>}
              {successMsg && <div className="text-green-500">{successMsg}</div>}
            </div>

            {/* Execute Button */}
            <button
              type="submit"
              disabled={loading}
              className={`
                group relative w-full overflow-hidden 
                py-4 font-bold tracking-[0.2em] uppercase
                border border-current
                transition-all duration-300 
                disabled:opacity-50 disabled:cursor-not-allowed mt-4
                ${tab === 'login' 
                  ? 'text-cyan-400 hover:bg-cyan-400 hover:text-black hover:border-cyan-400 hover:shadow-[0_0_20px_rgba(34,211,238,0.5)]' 
                  : tab === 'register'
                    ? 'text-purple-400 hover:bg-purple-400 hover:text-black hover:border-purple-400 hover:shadow-[0_0_20px_rgba(192,132,252,0.5)]'
                    : 'text-amber-500 hover:bg-amber-500 hover:text-black hover:border-amber-500 hover:shadow-[0_0_20px_rgba(245,158,11,0.5)]'
                }
              `}
            >
              <div className="relative z-10 flex items-center justify-center gap-2">
                {loading ? <Loader2 className="w-5 h-5 animate-spin" /> : <Zap className="w-5 h-5 group-hover:fill-current" />}
                {loading ? '处理中...' : (tab === 'login' ? '建立连接' : tab === 'register' ? '创建身份' : '重置密钥')}
                {!loading && <ArrowRight className="w-5 h-5 group-hover:translate-x-1 transition-transform" />}
              </div>
            </button>
          </form>

          {/* Footer */}
          <div className="absolute bottom-6 left-0 w-full px-8 md:px-24 text-xs text-gray-400 flex justify-between items-center font-mono">
            <span>V 1.0.1</span>
            <span className="flex items-center gap-2">
              <span className="w-2 h-2 rounded-full bg-green-500 animate-ping"></span>
              服务状态: 正常
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}
