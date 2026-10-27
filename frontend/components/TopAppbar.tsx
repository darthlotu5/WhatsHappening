import { SignInButton, SignUpButton, UserButton, useUser } from "@clerk/nextjs";
import Link from "next/link";
import React from "react";

const TopAppbar = () => {
  const { isLoaded, isSignedIn } = useUser();
  return (
    <nav className="bg-white-700 py-4 shadow-md">
      <div className="container flex justify-between mx-auto items-center">
        <h6 className="text-lg text-purple-700">Dundaa</h6>
        <div className="flex items-center gap-12">
          <Link href="/" className="text-sm text-black uppercase transition-all duration-300 hover:text-opacity-70 font-medium">
              Home
          </Link>
          <Link href="/events" className="text-sm text-black uppercase transition-all duration-300 hover:text-opacity-70 font-medium">
              All Events
          </Link>
          {isLoaded && (isSignedIn ? <UserButton /> : (
            <>
              <SignInButton><button>Sign in</button></SignInButton>
              <SignUpButton><button>Create account</button></SignUpButton>
            </>
          ))}
        </div>
      </div>
    </nav>
  );
};

export default TopAppbar;
