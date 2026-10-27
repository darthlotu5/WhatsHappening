import { SignUp } from "@clerk/nextjs";
import Head from "next/head";
import AuthContainer from "../../components/AuthContainer";

export default function Register() {
  return (
    <>
      <Head><title>Create account | Dundaa</title></Head>
      <AuthContainer pageTitle="Create Account">
        <SignUp path="/register" routing="path" signInUrl="/login" fallbackRedirectUrl="/" />
      </AuthContainer>
    </>
  );
}
