import React from "react";

/** Auth container */
const AuthContainer: React.FC<React.PropsWithChildren<{ pageTitle: string }>> = ({
  pageTitle,
  children,
}) => {
  return (
    <div className="grid h-screen w-screen grid-cols-2">
      <div className="grid place-items-center">
        <div className="w-full lg:w-1/2 px-4">
          <h1 className="text-2xl mb-3 text-center">{pageTitle}</h1>
          {children}
        </div>
      </div>

      <img
        src="/images/login.jpg"
        alt="Login"
        className="h-full w-full object-cover"
      />
    </div>
  );
};

export default AuthContainer;
