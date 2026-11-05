"use client";

/* The PayPal button for an order the server already created. The browser never sets the amount
   or the payee: it only asks PayPal to let the payer approve that order, then tells the server,
   which captures it. */

import { useEffect, useRef, useState } from "react";

type Buttons = {
  render: (el: HTMLElement) => Promise<void>;
  close?: () => Promise<void>;
  isEligible?: () => boolean;
};
type PayPalSdk = {
  Buttons: (options: {
    style?: Record<string, unknown>;
    createOrder: () => string | Promise<string>;
    onApprove: (data: { orderID: string }) => Promise<void>;
    onCancel?: () => void;
    onError?: (err: unknown) => void;
  }) => Buttons;
};

declare global {
  interface Window {
    paypal?: PayPalSdk;
  }
}

let loading: Promise<PayPalSdk> | null = null;
let loadedFor = "";

function loadSdk(clientId: string, currency: string): Promise<PayPalSdk> {
  const key = `${clientId}:${currency}`;
  if (loading && loadedFor === key) return loading;
  loadedFor = key;
  loading = new Promise<PayPalSdk>((resolve, reject) => {
    const script = document.createElement("script");
    const params = new URLSearchParams({ "client-id": clientId, currency, intent: "capture", components: "buttons" });
    script.src = `https://www.paypal.com/sdk/js?${params}`;
    script.async = true;
    script.dataset.namespace = "paypal";
    script.onload = () => (window.paypal ? resolve(window.paypal) : reject(new Error("PayPal did not load")));
    script.onerror = () => {
      loading = null;
      reject(new Error("Could not reach PayPal to show the button"));
    };
    document.head.appendChild(script);
  });
  return loading;
}

export function PayPalButton({
  clientId,
  currency,
  orderId,
  onApproved,
  onError,
}: {
  clientId: string;
  currency: string;
  orderId: string;
  onApproved: (orderId: string) => Promise<void>;
  onError: (message: string) => void;
}) {
  const box = useRef<HTMLDivElement>(null);
  const [ready, setReady] = useState(false);
  const approved = useRef(onApproved);
  const failed = useRef(onError);
  useEffect(() => {
    approved.current = onApproved;
    failed.current = onError;
  });

  useEffect(() => {
    let buttons: Buttons | null = null;
    let gone = false;
    loadSdk(clientId, currency)
      .then((paypal) => {
        if (gone || !box.current) return;
        buttons = paypal.Buttons({
          style: { layout: "horizontal", height: 32, tagline: false, label: "pay" },
          createOrder: () => orderId,
          onApprove: (data) => approved.current(data.orderID),
          onError: () => failed.current("PayPal could not complete the payment. Try again."),
        });
        return buttons.render(box.current).then(() => !gone && setReady(true));
      })
      .catch((err: Error) => failed.current(err.message));
    return () => {
      gone = true;
      buttons?.close?.().catch(() => undefined);
    };
  }, [clientId, currency, orderId]);

  return (
    <div className="min-w-48">
      <div ref={box} />
      {!ready && <p className="text-xs text-faint">Loading PayPal…</p>}
    </div>
  );
}
