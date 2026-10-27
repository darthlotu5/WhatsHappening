import { SignIn } from "@clerk/nextjs";
import Head from "next/head";
import AuthContainer from "../../components/AuthContainer";

export default function Login() {
  return (
    <>
      <Head><title>Sign in | Dundaa</title></Head>
      <AuthContainer pageTitle="Sign In">
        <SignIn path="/login" routing="path" signUpUrl="/register" fallbackRedirectUrl="/" />
      </AuthContainer>
    </>
  );
}
