import { Link } from 'react-router-dom';

export default function PageNotFound() {
  return (
    <div className="min-h-screen bg-slate-950 flex items-center justify-center">
      <div className="text-center">
        <h1 className="text-6xl font-bold text-slate-500 mb-4">404</h1>
        <p className="text-slate-400 mb-6">Page not found</p>
        <Link to="/" className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors">
          Go Home
        </Link>
      </div>
    </div>
  );
}