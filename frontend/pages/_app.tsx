import { ClerkProvider } from "@clerk/nextjs";
import type { AppProps } from "next/app";
import "../styles/tailwind.css";

export default function MyApp({ Component, pageProps }: AppProps) {
  return (
    <ClerkProvider {...pageProps} signInUrl="/login" signUpUrl="/register">
      <Component {...pageProps} />
    </ClerkProvider>
  );
}
