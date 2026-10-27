import { Alert, Button } from "@mui/material";
import { useRouter } from "next/router";
import React from "react";
import { DEFAULT_ERR_MESSAGE } from "../utils/constants";

const FetchError: React.FC<{ errorMessage?: string; reload?: boolean }> = ({
  reload = false,
  errorMessage = DEFAULT_ERR_MESSAGE,
}) => {
  const router = useRouter();
  return (
    <div className="container mt-4 mx-auto grid gap-2">
      <Alert severity="error">{errorMessage}</Alert>
      <div>
        {reload && (
          <Button
            variant="contained"
            color="primary"
            onClick={() => router.reload()}
          >
            Reload
          </Button>
        )}
      </div>
    </div>
  );
};

export default FetchError;
