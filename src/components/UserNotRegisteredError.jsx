export default function UserNotRegisteredError() {
  return (
    <div className="flex items-center justify-center min-h-screen bg-slate-950">
      <div className="text-center text-slate-400">
        <p className="text-lg font-semibold text-white">Access Restricted</p>
        <p className="text-sm mt-2">Your account has not been registered for this app.</p>
      </div>
    </div>
  );
}
