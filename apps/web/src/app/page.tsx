"use client";

import { PRODUCT } from "@anglecast/shared";
import { motion } from "framer-motion";
import { useRouter } from "next/navigation";
import { FormEvent, useState } from "react";

function slugifyRoom(value: string) {
  return value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9-]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 48);
}

export default function HomePage() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [room, setRoom] = useState("");

  function onJoin(e: FormEvent) {
    e.preventDefault();
    const roomName = slugifyRoom(room) || `studio-${Math.random().toString(36).slice(2, 8)}`;
    const displayName = name.trim() || "Guest";
    const params = new URLSearchParams({ name: displayName });
    router.push(`/room/${encodeURIComponent(roomName)}?${params.toString()}`);
  }

  return (
    <main className="relative min-h-screen overflow-hidden bg-ink-900 bg-studio-glow">
      <div className="pointer-events-none absolute inset-0 bg-grain opacity-40" />
      <div className="relative mx-auto flex min-h-screen max-w-5xl flex-col px-6 pb-16 pt-10">
        <header className="flex items-center justify-between">
          <motion.div
            initial={{ opacity: 0, y: -8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.5 }}
            className="font-display text-2xl tracking-tight text-mist-100"
          >
            {PRODUCT.name}
          </motion.div>
          <span className="text-sm text-mist-500">Cloud studio · Phase 1</span>
        </header>

        <section className="mt-auto flex flex-1 flex-col justify-center gap-12 py-16 md:flex-row md:items-end md:justify-between">
          <div className="max-w-xl">
            <motion.h1
              initial={{ opacity: 0, y: 16 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.6, delay: 0.05 }}
              className="font-display text-4xl leading-[1.05] tracking-tight text-mist-100 sm:text-5xl md:text-6xl"
            >
              {PRODUCT.name}
            </motion.h1>
            <motion.p
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.55, delay: 0.15 }}
              className="mt-5 text-lg text-mist-300 text-balance"
            >
              {PRODUCT.tagline}
            </motion.p>
            <motion.p
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ duration: 0.5, delay: 0.28 }}
              className="mt-3 text-sm text-mist-500"
            >
              {PRODUCT.subTagline}
            </motion.p>
          </div>

          <motion.form
            onSubmit={onJoin}
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.55, delay: 0.2 }}
            className="w-full max-w-md rounded-2xl bg-ink-800/80 p-6 shadow-panel backdrop-blur-md"
          >
            <label className="block text-xs font-medium uppercase tracking-wider text-mist-500">
              Your name
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Alex Producer"
                className="mt-2 w-full rounded-xl border border-white/10 bg-ink-900 px-4 py-3 text-base text-mist-100 outline-none ring-brass-400/40 placeholder:text-mist-500 focus:ring-2"
                autoComplete="name"
              />
            </label>
            <label className="mt-4 block text-xs font-medium uppercase tracking-wider text-mist-500">
              Room name
              <input
                value={room}
                onChange={(e) => setRoom(e.target.value)}
                placeholder="spring-episode-12"
                className="mt-2 w-full rounded-xl border border-white/10 bg-ink-900 px-4 py-3 text-base text-mist-100 outline-none ring-brass-400/40 placeholder:text-mist-500 focus:ring-2"
                autoComplete="off"
              />
            </label>
            <button
              type="submit"
              className="mt-6 w-full rounded-xl bg-brass-400 px-4 py-3 text-sm font-semibold text-ink-950 transition hover:bg-brass-300"
            >
              Enter studio
            </button>
            <p className="mt-3 text-center text-xs text-mist-500">
              Camera & mic permissions required. Recording is never silent — you&apos;ll always see a live
              indicator.
            </p>
          </motion.form>
        </section>
      </div>
    </main>
  );
}
